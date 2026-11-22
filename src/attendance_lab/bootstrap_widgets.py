"""Temporary QWidget rollout fallback, retained until physical-reader acceptance."""

import logging
import sys
from logging.handlers import RotatingFileHandler


def run_widgets(args) -> int:
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from attendance_lab.application.controller import AttendanceController
    from attendance_lab.application.service import AttendanceService
    from attendance_lab.bootstrap import default_database
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
        window.showMaximized()
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
