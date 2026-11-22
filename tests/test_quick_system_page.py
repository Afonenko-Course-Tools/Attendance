"""The system page uses explicit confirmation and stable archive identities."""


def test_system_page_loads_and_preserves_failed_forms_and_restore_review():
    from pathlib import Path

    from PySide6.QtCore import Property, QCoreApplication, QEvent, QObject, QUrl, Signal, Slot
    from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtWidgets import QApplication

    from attendance_lab.presentation.quick.models import RecordListModel

    lesson = {
        "id": 9,
        "discipline_id": 3,
        "title": "Архивное занятие",
        "room": "301",
        "starts_at": "2026-09-01T09:00:00+03:00",
        "ends_at": "2026-09-01T10:30:00+03:00",
        "timezone": "Europe/Minsk",
        "state": "completed",
    }
    student = {"id": "stable-id", "full_name": "Синтетический студент", "group": "1"}
    system = {
        "readers": ["Synthetic PCSC"],
        "reader_status": "Считыватель не выбран",
        "demo": False,
        "disciplines": [{"id": 3, "name": "Синтетическая дисциплина", "description": ""}],
        "archive": [lesson],
        "archive_lesson_id": 9,
        "archive_roster": ["stable-id"],
        "archive_students": [student],
        "archive_log": [],
        "recovered": 0,
    }

    class SystemTestScreen(QObject):
        changed = Signal()
        commandSucceeded = Signal(str, "QVariantMap")
        commandFailed = Signal(str, str)

        def __init__(self):
            super().__init__()
            self._drafts = {}
            self._registration = {"open": False, "pending_count": 0}
            self.calls = []
            self.models = [RecordListModel(self) for _ in range(4)]
            for model, rows in zip(self.models, ([lesson], [], [student], []), strict=True):
                model.set_rows(rows)

        state = Property(
            "QVariantMap",
            lambda self: {"system": system, "registration": self._registration},
            notify=changed,
        )
        drafts = Property("QVariantMap", lambda self: self._drafts, notify=changed)
        selectedLessonId = Property(int, lambda self: 0, constant=True)
        busy = Property(bool, lambda self: False, constant=True)
        canOpenLegacy = Property(bool, lambda self: True, constant=True)
        canClose = Property(bool, lambda self: False, constant=True)
        systemArchiveRows = Property(QObject, lambda self: self.models[0], constant=True)
        systemArchiveLogRows = Property(QObject, lambda self: self.models[1], constant=True)
        systemStudentRows = Property(QObject, lambda self: self.models[2], constant=True)
        logRows = Property(QObject, lambda self: self.models[3], constant=True)

        @Slot(str, str, "QVariant")
        def setDraft(self, scope, key, value):
            value = value.toVariant() if hasattr(value, "toVariant") else value
            self._drafts.setdefault(scope, {})[key] = value
            self.changed.emit()

        @Slot(str, "QVariantMap")
        def command(self, name, payload):
            self.calls.append((name, payload))

    class SystemTestWindows(QObject):
        changed = Signal()
        screens = Property("QStringList", lambda self: ["Synthetic screen"], constant=True)
        publicVisible = Property(bool, lambda self: False, notify=changed)
        timeoutSeconds = Property(int, lambda self: 3, notify=changed)

        @Slot()
        def togglePublic(self):
            pass

        @Slot(int)
        def setScreen(self, index):
            pass

        @Slot(bool)
        def setFullscreen(self, enabled):
            pass

        @Slot(int)
        def setTimeout(self, value):
            pass

    app = QApplication.instance() or QApplication([])
    screen, windows = SystemTestScreen(), SystemTestWindows()
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(
            str(Path(__file__).parents[1] / "src/attendance_lab/presentation/quick/SystemPage.qml")
        ),
    )
    assert not component.errors(), [error.toString() for error in component.errors()]
    page = component.createWithInitialProperties({"app": screen, "windows": windows})
    assert page is not None, [error.toString() for error in component.errors()]
    window = QQuickWindow()
    window.resize(1200, 900)
    page.setParentItem(window.contentItem())
    page.setWidth(1200)
    page.setHeight(900)
    window.show()

    def invoke(scope, text):
        expression = QQmlExpression(engine.rootContext(), scope, text, engine)
        expression.evaluate()
        assert not expression.hasError(), expression.error().toString()
        app.processEvents()

    try:
        app.processEvents()
        assert page.findChild(QObject, "systemArchive") is not None
        assert page.findChild(QObject, "systemPrivateLog") is not None
        invoke(page, "openArchive()")
        assert screen.calls[-1] == ("registration.open_legacy", {"lesson_id": 9})
        invoke(page, "editLegacy(app.state.system.archive[0])")
        legacy_dialog = page.findChild(QObject, "legacyDialog")
        assert legacy_dialog.property("visible") and page.property("modalOpen")
        invoke(page, "saveLegacy()")
        assert screen.calls[-1][0] == "system.save_legacy"
        assert screen.calls[-1][1]["lesson_id"] == 9
        screen.commandFailed.emit("system.save_legacy", "Синтетическая ошибка")
        assert legacy_dialog.property("visible")
        assert screen._drafts["legacyForm"]["title"] == lesson["title"]
        screen.commandSucceeded.emit("system.save_legacy", {"lesson_id": 9})
        assert not legacy_dialog.property("visible")
        invoke(page, 'reviewRestore("file:///tmp/selected%20backup.sqlite3")')
        review = page.findChild(QObject, "restoreReview")
        restore_button = page.findChild(QObject, "confirmRestore")
        assert review.property("visible")
        assert (
            page.findChild(QObject, "restorePath").property("text")
            == "/tmp/selected backup.sqlite3"
        )
        assert not restore_button.property("enabled")
        screen.setDraft("restore", "confirmed", True)
        assert restore_button.property("enabled")
        screen._registration["open"] = True
        screen.changed.emit()
        assert not restore_button.property("enabled")
        screen._registration = {"open": False, "pending_count": 1}
        screen.changed.emit()
        assert not restore_button.property("enabled")
        screen.commandFailed.emit("system.restore", "Синтетическая ошибка")
        assert review.property("visible")
        assert screen._drafts["restore"]["path"] == "file:///tmp/selected%20backup.sqlite3"
        assert not warnings, warnings
    finally:
        page.deleteLater()
        window.close()
        window.deleteLater()
        engine.deleteLater()
        screen.deleteLater()
        windows.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
