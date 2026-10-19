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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Локальный учёт посещаемости по PC/SC-картам")
    parser.add_argument(
        "--demo", action="store_true", help="Имитатор карт с отдельной базой данных"
    )
    parser.add_argument("--db", type=Path, help="Путь к базе (режим базы real/demo проверяется)")
    parser.add_argument(
        "--timezone", default=local_timezone(), help="Часовой пояс по умолчанию для новых занятий"
    )
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError, ValueError:
        parser.error("Неизвестный часовой пояс. Например: Europe/Minsk или UTC")

    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from attendance_lab.application.controller import AttendanceController
    from attendance_lab.application.service import AttendanceService
    from attendance_lab.infrastructure.csv_io import CsvFiles
    from attendance_lab.infrastructure.readers import DemoReader, PCSCReader
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
            SQLiteRepository(database, mode="demo" if args.demo else "real")
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
