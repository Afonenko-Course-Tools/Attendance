"""Composition root: runtime settings and concrete adapters are joined only here."""

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def local_timezone() -> str:
    candidates = [os.environ.get("TZ", "")]
    localtime = Path("/etc/localtime").resolve()
    if "/zoneinfo/" in str(localtime):
        candidates.append(str(localtime).split("/zoneinfo/", 1)[1])
    try:
        candidates.append(Path("/etc/timezone").read_text().strip())
    except OSError:
        pass
    for candidate in candidates:
        try:
            ZoneInfo(candidate)
            return candidate
        except ZoneInfoNotFoundError, ValueError:
            continue
    return "UTC"


def default_database(demo: bool) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return base / "attendance-lab" / ("demo.sqlite3" if demo else "attendance.sqlite3")


def migrate_database(
    database: Path,
    *,
    demo: bool,
    rollback_from: Path | None = None,
    install_from: Path | None = None,
) -> int:
    """Upgrade an existing database without a display, under the application lock."""
    from PySide6.QtCore import QCoreApplication, QLockFile

    from attendance_lab.infrastructure.sqlite import SCHEMA_VERSION, SQLiteRepository

    application = QCoreApplication.instance() or QCoreApplication([sys.argv[0]])
    application.setApplicationName("Attendance Lab migration")
    if not database.is_file():
        print("Для миграции выберите существующую базу данных.", file=sys.stderr)
        return 1
    lock = QLockFile(str(database) + ".lock")
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print(
            "База используется приложением. Завершите приложение перед миграцией.", file=sys.stderr
        )
        return 1
    try:
        if install_from is not None:
            from attendance_lab.infrastructure.migration_plan import install_approved_copy

            install_approved_copy(database, install_from, mode="demo" if demo else "real")
            print(
                "Утверждённый перенос установлен. Предыдущая версия сохранена в страховочной копии."
            )
        elif rollback_from is not None:
            from attendance_lab.infrastructure.migration import rollback

            rollback(database, rollback_from, mode="demo" if demo else "real")
            print("База возвращена к версии резервной копии. Запускайте прежнюю сборку.")
        else:
            repository = SQLiteRepository(database, mode="demo" if demo else "real")
            repository.close()
            print(f"База проверена; версия схемы: {SCHEMA_VERSION}.")
        return 0
    except Exception:
        print(
            "Миграция не выполнена. Исходная база сохранена; проверьте выбранные файлы.",
            file=sys.stderr,
        )
        return 1
    finally:
        lock.unlock()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Локальный учёт посещаемости по PC/SC-картам")
    parser.add_argument(
        "--demo", action="store_true", help="Имитатор карт с отдельной базой данных"
    )
    parser.add_argument("--db", type=Path, help="Путь к базе (режим базы real/demo проверяется)")
    migration_actions = parser.add_mutually_exclusive_group()
    migration_actions.add_argument(
        "--migrate-only", action="store_true", help="Проверить и обновить существующую БД без GUI"
    )
    migration_actions.add_argument(
        "--rehearse-migration",
        type=Path,
        metavar="NEW_DIRECTORY",
        help="Репетиция на приватной копии без изменения рабочей базы",
    )
    migration_actions.add_argument(
        "--rollback-from",
        type=Path,
        metavar="BACKUP",
        help="Вернуть прежнюю схему из копии (приложение должно быть закрыто)",
    )
    migration_actions.add_argument(
        "--apply-migration-plan",
        type=Path,
        metavar="APPROVED_JSON",
        help="Проверить утверждённый локальный план и применить только к новой копии",
    )
    migration_actions.add_argument(
        "--install-migration-copy",
        type=Path,
        metavar="CANDIDATE",
        help="Установить утверждённую копию после закрытия приложения",
    )
    parser.add_argument(
        "--migration-output",
        type=Path,
        metavar="NEW_DIRECTORY",
        help="Новый приватный каталог результата применения плана",
    )
    parser.add_argument(
        "--timezone", default=local_timezone(), help="Часовой пояс по умолчанию для новых занятий"
    )
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError, ValueError:
        parser.error("Неизвестный часовой пояс. Например: Europe/Minsk или UTC")

    if args.apply_migration_plan:
        if args.migration_output is None:
            parser.error("Для применения плана укажите --migration-output NEW_DIRECTORY")
        from attendance_lab.infrastructure.migration_plan import apply_approved

        database = (args.db or default_database(args.demo)).expanduser().resolve()
        try:
            result = apply_approved(
                database,
                args.apply_migration_plan,
                args.migration_output,
                mode="demo" if args.demo else "real",
            )
            print(
                f"План применён к новой копии: {result['blocks']} блоков. Рабочая база не изменена."
            )
            return 0
        except Exception:
            print(
                "План не применён: проверьте заполнение решений и соответствие исходной копии. "
                "Рабочая база не изменена.",
                file=sys.stderr,
            )
            return 1

    if args.rehearse_migration:
        from attendance_lab.infrastructure.migration import rehearse

        database = (args.db or default_database(args.demo)).expanduser().resolve()
        try:
            report = rehearse(
                database, args.rehearse_migration, mode="demo" if args.demo else "real"
            )
            print(
                f"Репетиция завершена. Неоднозначных занятий: {len(report['legacy_plan'])}. "
                "Приватный план сохранён в выбранном каталоге."
            )
            return 0
        except Exception:
            print("Репетиция не выполнена; рабочая база не изменена.", file=sys.stderr)
            return 1

    if args.migrate_only or args.rollback_from or args.install_migration_copy:
        database = (args.db or default_database(args.demo)).expanduser().resolve()
        return migrate_database(
            database,
            demo=args.demo,
            rollback_from=args.rollback_from,
            install_from=args.install_migration_copy,
        )

    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from attendance_lab.application.controller import AttendanceController
    from attendance_lab.application.service import AttendanceService
    from attendance_lab.infrastructure.csv_io import CsvFiles
    from attendance_lab.infrastructure.readers import DemoReader, PCSCReader
    from attendance_lab.infrastructure.report_topic_catalog import load_report_topic_catalogs
    from attendance_lab.infrastructure.sqlite import SQLiteRepository
    from attendance_lab.presentation.admin import MainWindow
    from attendance_lab.presentation.theme import apply_light_theme

    app = QApplication([sys.argv[0]])
    apply_light_theme(app)
    app.setApplicationName("Attendance Lab")
    app.setOrganizationName("Attendance Lab")
    database = (args.db or default_database(args.demo)).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(database) + ".lock")
    if not lock.tryLock(100):
        QMessageBox.critical(
            None, "База уже открыта", "Для этой базы уже работает другой экземпляр приложения."
        )
        return 1

    handler = RotatingFileHandler(
        database.with_suffix(".log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("attendance_lab").addHandler(handler)
    logging.getLogger("attendance_lab").setLevel(logging.INFO)
    service = None
    controller = None
    try:
        service = AttendanceService(
            SQLiteRepository(database, mode="demo" if args.demo else "real"),
            report_topic_catalogs=load_report_topic_catalogs(),
        )
        controller = AttendanceController(service, DemoReader if args.demo else PCSCReader)
        recovered = service.recover_interrupted()
        window = MainWindow(
            service,
            controller,
            files=CsvFiles(),
            demo=args.demo,
            database_path=database,
            default_timezone=args.timezone,
        )
        window.show()
        if recovered:
            QTimer.singleShot(
                0,
                lambda: QMessageBox.information(
                    window,
                    "Восстановление после прерывания",
                    f"Закрыта прерванная регистрация: {recovered}. Сохранённые отметки доступны. "
                    "Для продолжения выберите занятие и откройте регистрацию снова.",
                ),
            )
        if args.smoke_test:
            QTimer.singleShot(400, window.close)
        return app.exec()
    except Exception as exc:
        logging.getLogger(__name__).exception("Application startup failed")
        QMessageBox.critical(None, "Не удалось запустить приложение", str(exc))
        return 1
    finally:
        try:
            if controller is not None:
                controller.shutdown()
        except Exception:
            logging.getLogger(__name__).exception("Could not finalize active lesson at shutdown")
        finally:
            try:
                if service is not None:
                    service.close()
            finally:
                handler.close()
                logging.getLogger("attendance_lab").removeHandler(handler)
                lock.unlock()
