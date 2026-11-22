"""Production chart validation, reactive reads and public QML capability boundary."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QThread, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from quick_test_support import open_backend, seed_workspace
from reactivex.testing import TestScheduler
from shiboken6 import isValid

from attendance_lab.domain.models import PublicResult
from attendance_lab.presentation.quick.charts import load_chart
from attendance_lab.presentation.quick.public_projection import PublicProjection
from attendance_lab.presentation.quick.workspace import Workspace


def wait_for(predicate, timeout_ms=4000):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        assert time.monotonic() < deadline, "Production Quick runtime did not settle"
        QTest.qWait(5)
        time.sleep(0.001)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("source", [b"<invalid/>", b"not XML"])
def test_invalid_chart_fails_before_any_worker_is_created(app, source):
    parent = QObject()
    try:
        with pytest.raises(ValueError, match="Invalid SCXML chart broken.scxml"):
            load_chart(parent, "broken.scxml", source)
        assert not parent.findChildren(QThread)
    finally:
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_packaged_production_chart_is_initialized_and_owned(app):
    parent = QObject()
    machine = load_chart(parent, "workspace.scxml")
    try:
        assert machine.isInitialized() and not machine.parseErrors()
        assert machine.parent() == parent
        machine.start()
        wait_for(lambda: machine.isActive("noLesson") and machine.isActive("commandIdle"))
    finally:
        machine.stop()
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(machine)


@pytest.fixture
def reactive_workspace(app, tmp_path):
    path = tmp_path / "production-rx.sqlite3"
    ids = seed_workspace(path)
    scheduler = TestScheduler()
    model = Workspace(lambda: open_backend(path), search_scheduler=scheduler)
    try:
        wait_for(lambda: model.ready or model.error)
        assert model.ready, model.error
        yield model, ids, scheduler
    finally:
        if model.worker_thread.isRunning():
            model.requestClose()
            wait_for(lambda: not model.worker_thread.isRunning())
        model.dispose()
        model.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_production_debounce_keeps_latest_read_and_delivers_on_gui_thread(reactive_workspace):
    model, ids, scheduler = reactive_workspace
    sent, owners = [], []
    model.request.connect(sent.append)
    model.request.connect(
        lambda _: owners.append(QThread.currentThread()), Qt.ConnectionType.DirectConnection
    )
    model.command("grading.topic_search", {"course_id": ids["courses"][0], "query": "first"})
    scheduler.advance_by(0.05)
    assert sent == []
    model.command("grading.topic_search", {"course_id": ids["courses"][0], "query": "latest"})
    scheduler.advance_by(0.06)
    assert sent == []
    scheduler.advance_by(0.05)
    assert len(sent) == 1
    assert json.loads(sent[0].operation.payload_json)["query"] == "latest"
    wait_for(lambda: not model.busy)
    assert model.state["grading"]["query"] == "latest"
    assert owners == [model.thread()]


def test_shutdown_disposes_pending_production_debounce_before_delivery(reactive_workspace):
    model, ids, scheduler = reactive_workspace
    sent = []
    model.request.connect(sent.append)
    model.command("grading.topic_search", {"course_id": ids["courses"][0], "query": "pending"})
    model.requestClose()
    wait_for(lambda: model.shutdownComplete and not model.worker_thread.isRunning())
    model.dispose()
    scheduler.advance_by(1)
    assert sent == []
    assert not model.machine.isRunning()


def test_invalid_registration_and_matching_intents_start_no_effect(reactive_workspace):
    model, _, _ = reactive_workspace
    sent = []
    model.request.connect(sent.append)
    assert not model.canOpen and not model.canClose and not model.canSaveMatch
    for name in ("registration.open", "registration.close", "registration.bind"):
        model.command(name, {})
    model.machine.submitEvent("intent.close")
    model.machine.submitEvent("intent.match")
    QTest.qWait(10)
    assert sent == []
    assert model.machine.isActive("noLesson") and model.machine.isActive("matchIdle")


def test_public_qml_has_only_safe_projection_and_plain_text_without_private_commands(app):
    projection = PublicProjection()
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(
            str(
                Path(__file__).parents[1]
                / "src/attendance_lab/presentation/quick/PublicDisplay.qml"
            )
        ),
    )
    assert not component.errors(), [error.toString() for error in component.errors()]
    window = component.createWithInitialProperties({"projection": projection})
    assert window is not None, [error.toString() for error in component.errors()]
    try:
        meta = projection.metaObject()
        first_property = QObject.staticMetaObject.propertyCount()
        properties = {meta.property(i).name() for i in range(first_property, meta.propertyCount())}
        assert properties == {"context", "headline", "person", "detail", "color"}
        assert all(
            not meta.property(i).isWritable() for i in range(first_property, meta.propertyCount())
        )
        assert engine.rootContext().contextProperty("app") is None
        projection.set_context("Синтетическое занятие", True)
        projection.display(
            PublicResult(
                "success", "UID=0011 /private.sqlite3", "<b>Учебный</b>", datetime.now(UTC)
            )
        )
        app.processEvents()
        labels = [
            obj for obj in window.findChildren(QObject) if obj.property("text") == "<b>Учебный</b>"
        ]
        assert labels
        for label in labels:
            expression = QQmlExpression(
                QQmlEngine.contextForObject(label), label, "textFormat === Text.PlainText", engine
            )
            assert expression.evaluate()[0] is True, expression.error().toString()
        projection.display(
            PublicResult("error", "UID=0011 SQL failure /private.sqlite3", "Private Name")
        )
        app.processEvents()
        exposed = " ".join((projection.headline, projection.person, projection.detail))
        assert all(secret not in exposed for secret in ("0011", "SQL", "/private", "Private Name"))
        projection.display(PublicResult("success", "", None))
        assert "Преподаватель сопоставит позже" in projection.detail
        projection.idle()
        assert projection.person == ""
        assert not warnings, warnings
    finally:
        projection.timer.stop()
        window.deleteLater()
        engine.deleteLater()
        projection.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_shell_private_shortcuts_stop_for_public_focus_and_modal(reactive_workspace, app):
    from PySide6.QtGui import QWindow
    from PySide6.QtQuick import QQuickWindow

    from attendance_lab.presentation.quick.window_controls import WindowControls

    model, ids, _ = reactive_workspace
    model.selectLesson(ids["lessons"][0])
    wait_for(lambda: model.canOpen)
    projection = PublicProjection()
    public_window = QQuickWindow()
    controls = WindowControls(public_window, projection)
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(
            str(Path(__file__).parents[1] / "src/attendance_lab/presentation/quick/Application.qml")
        ),
    )
    model.bind_qml_engine(engine)
    assert not component.errors(), [error.toString() for error in component.errors()]
    private_window = component.createWithInitialProperties({"app": model, "windows": controls})
    assert private_window is not None, [error.toString() for error in component.errors()]
    sent = []
    model.request.connect(sent.append)
    try:
        navigation = private_window.findChild(QObject, "workspaceNavigation")
        labels = []
        for index in range(navigation.property("count")):
            expression = QQmlExpression(
                QQmlEngine.contextForObject(navigation),
                navigation,
                f"itemAt({index}).text",
                engine,
            )
            labels.append(expression.evaluate()[0])
        assert labels == ["Занятие", "Посещаемость", "Оценки", "Курс"]
        menu = private_window.findChild(QObject, "mainMenuBar")
        assert menu.property("count") == 4
        menu_fonts = QQmlExpression(
            QQmlEngine.contextForObject(menu),
            menu,
            "[itemAt(0).font.pixelSize, menuAt(0).itemAt(0).font.pixelSize, "
            "menuAt(2).itemAt(4).subMenu.itemAt(0).font.pixelSize].join(',')",
            engine,
        )
        assert menu_fonts.evaluate()[0] == "18,18,18", menu_fonts.error().toString()
        assert not private_window.findChild(QObject, "studentsWindow").isVisible()
        assert not private_window.findChild(QObject, "settingsWindow").isVisible()
        private_window.requestActivate()
        wait_for(lambda: private_window.isActive() and private_window.property("privateCommands"))
        assert private_window.visibility() == QWindow.Visibility.Maximized
        QTest.keyClick(private_window, Qt.Key.Key_F11)
        wait_for(lambda: private_window.visibility() == QWindow.Visibility.FullScreen)
        QTest.keyClick(private_window, Qt.Key.Key_F11)
        wait_for(lambda: private_window.visibility() == QWindow.Visibility.Maximized)
        zoom = controls.interfaceZoom
        initial_height = navigation.property("height")
        QTest.keyClick(private_window, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        wait_for(lambda: zoom.percent == 110 and navigation.property("height") > initial_height)
        assert menu_fonts.evaluate()[0] == "20,20,20", menu_fonts.error().toString()
        QTest.keyClick(private_window, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        wait_for(lambda: zoom.percent == 100 and navigation.property("height") == initial_height)
        public_window.show()
        public_window.requestActivate()
        wait_for(lambda: public_window.isActive() and not private_window.isActive())
        assert not private_window.property("privateCommands")
        QTest.keyClick(public_window, Qt.Key.Key_F9)
        QTest.keyClick(public_window, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(10)
        assert zoom.percent == 100
        assert not any(request.operation.kind == "registration.open" for request in sent)
        private_window.requestActivate()
        wait_for(lambda: private_window.isActive())
        system_page = private_window.findChild(QObject, "systemPage")
        expression = QQmlExpression(
            QQmlEngine.contextForObject(system_page),
            system_page,
            'reviewRestore("file:///tmp/review-only.sqlite3")',
            engine,
        )
        expression.evaluate()
        assert not expression.hasError(), expression.error().toString()
        wait_for(lambda: private_window.property("modalOpen"))
        assert not private_window.property("privateCommands")
        QTest.keyClick(private_window, Qt.Key.Key_F9)
        QTest.qWait(10)
        assert not any(request.operation.kind == "registration.open" for request in sent)
        review = system_page.findChild(QObject, "restoreReview")
        expression = QQmlExpression(QQmlEngine.contextForObject(review), review, "close()", engine)
        expression.evaluate()
        wait_for(lambda: private_window.property("privateCommands"))
        QTest.keyClick(private_window, Qt.Key.Key_F9)
        wait_for(lambda: model.machine.isActive("open") and not model.busy)
        assert sum(request.operation.kind == "registration.open" for request in sent) == 1
        assert not warnings, warnings
    finally:
        # Native views die while their QObject dependencies and GUI loop still exist.
        private_window.deleteLater()
        public_window.deleteLater()
        engine.deleteLater()
        controls.deleteLater()
        projection.timer.stop()
        projection.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_private_engine_keeps_one_script_snapshot_until_data_actually_changes(reactive_workspace):
    """Large-table delegates must not deep-convert a QVariantMap per field read."""
    import copy

    model, _, _ = reactive_workspace
    engine = QQmlEngine()
    changes = []
    model.stateChanged.connect(lambda: changes.append(True))
    model.bind_qml_engine(engine)
    engine.globalObject().setProperty("workspace", engine.newQObject(model))
    assert not engine.evaluate("var savedSnapshot = workspace.qmlState").isError()
    try:
        assert engine.evaluate("savedSnapshot.course.id").toInt() == model.selectedCourseId
        baseline = len(changes)
        model.setDraft("registration", "query", "editing only")
        model.machine.submitEvent("match.edit")
        QTest.qWait(10)
        assert len(changes) == baseline
        assert engine.evaluate("savedSnapshot === workspace.qmlState").toBool()
        model._apply_snapshot(copy.deepcopy(model.snapshot()))
        assert len(changes) == baseline
        assert engine.evaluate("savedSnapshot === workspace.qmlState").toBool()
        updated = copy.deepcopy(model.snapshot())
        updated["system"]["reader_status"] = "Synthetic new status"
        model._apply_snapshot(updated)
        assert len(changes) == baseline + 1
        assert not engine.evaluate("savedSnapshot === workspace.qmlState").toBool()
        assert engine.evaluate("workspace.qmlState.system.reader_status").toString() == (
            "Synthetic new status"
        )
        # Python application/tests retain ordinary transport values.
        assert model.state["system"]["reader_status"] == "Synthetic new status"
        other = QQmlEngine()
        try:
            with pytest.raises(RuntimeError, match="one private QML engine"):
                model.bind_qml_engine(other)
        finally:
            other.deleteLater()
    finally:
        model.requestClose()
        wait_for(lambda: model.shutdownComplete)
        model.dispose()
        assert model.qmlState.isUndefined()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
