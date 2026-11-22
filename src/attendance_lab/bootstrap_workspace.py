"""Production composition root. Storage is constructed inside the worker factory."""

import logging
import sys
from importlib.resources import files
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QLockFile, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.quick_backend import QuickBackend
from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DemoReader, PCSCReader
from attendance_lab.infrastructure.report_topic_catalog import load_report_topic_catalogs
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.quick.public_projection import PublicProjection
from attendance_lab.presentation.quick.theme import apply_light_theme
from attendance_lab.presentation.quick.window_controls import WindowControls
from attendance_lab.presentation.quick.workspace import Workspace


def run_workspace(args) -> int:
    from attendance_lab.bootstrap import default_database

    app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    assert isinstance(app, QGuiApplication)
    app.setApplicationName("Attendance Lab")
    app.setOrganizationName("Attendance Lab")
    apply_light_theme(app)
    database = Path(args.db or default_database(args.demo)).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(database) + ".lock")
    if not lock.tryLock(100):
        print("Для этой базы уже работает другой экземпляр приложения.", file=sys.stderr)
        return 1
    handler = RotatingFileHandler(
        database.with_suffix(".log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("attendance_lab")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def factory():
        # This callable runs on attendance-storage-owner. No open connection crosses threads.
        service = AttendanceService(
            SQLiteRepository(database, mode="demo" if args.demo else "real"),
            report_topic_catalogs=load_report_topic_catalogs(),
        )
        try:
            controller = AttendanceController(service, DemoReader if args.demo else PCSCReader)
            return QuickBackend(
                service, controller, CsvFiles(), demo=args.demo, timezone=args.timezone
            )
        except BaseException:
            service.close()
            raise

    workspace = Workspace(factory)
    projection = PublicProjection()
    public_engine = QQmlApplicationEngine()
    public_engine.setInitialProperties({"projection": projection})
    resource = files("attendance_lab.presentation.quick")
    public_engine.load(QUrl.fromLocalFile(str(resource.joinpath("PublicDisplay.qml"))))
    engine = QQmlApplicationEngine()
    workspace.bind_qml_engine(engine)
    controls = None
    exit_code = 1
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    if public_engine.rootObjects():
        controls = WindowControls(public_engine.rootObjects()[0], projection)
        engine.setInitialProperties({"app": workspace, "windows": controls})
        engine.load(QUrl.fromLocalFile(str(resource.joinpath("Application.qml"))))
    workspace.publicResult.connect(projection.display)

    def synchronize_public():
        registration = workspace.snapshot().get("registration", {})
        projection.set_context(registration.get("active_label", ""), bool(registration.get("open")))

    workspace.changed.connect(synchronize_public)
    workspace.shutdownReady.connect(app.quit)
    try:
        if not engine.rootObjects():
            for warning in warnings:
                print(warning, file=sys.stderr)
            workspace.requestClose()
            loop = QEventLoop()
            workspace.shutdownReady.connect(loop.quit)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            return 1
        if args.smoke_test:

            def finish_smoke():
                if workspace.ready:
                    workspace.requestClose()
                elif workspace.error:
                    workspace.requestClose()
                else:
                    QTimer.singleShot(30, finish_smoke)

            QTimer.singleShot(300, finish_smoke)
        exit_code = app.exec()
        if warnings or workspace.error:
            exit_code = 1
        return exit_code
    finally:
        if workspace.worker_thread.isRunning():
            # Window closure normally completes before app.quit; external quits also drain.
            loop = QEventLoop()
            workspace.shutdownReady.connect(loop.quit)
            workspace.requestClose()
            loop.exec()
        workspace.dispose()
        for root in engine.rootObjects() + public_engine.rootObjects():
            root.deleteLater()
        engine.deleteLater()
        public_engine.deleteLater()
        if controls is not None:
            controls.deleteLater()
        projection.deleteLater()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        handler.close()
        logger.removeHandler(handler)
        lock.unlock()
