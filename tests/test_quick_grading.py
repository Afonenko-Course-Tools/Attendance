"""Production assessment actions keep domain behavior independent of Qt."""

import json
from datetime import date

import pytest

from attendance_lab.application.operations import canonical_json
from attendance_lab.application.quick_courses import CourseActions
from attendance_lab.application.quick_grading import MUTATING_COMMANDS, GradingActions
from attendance_lab.application.quick_students import StudentActions
from attendance_lab.application.report_topics import ReportTopic, ReportTopicCatalog
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.grading import CRITERIA
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def grading(tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "quick-grading.sqlite3"))
    headers = (
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    )
    rows = [
        ["1", "Студент Первый", "Тест", "Профиль", "2", "1", "00001"],
        ["2", "Студент Второй", "Тест", "Профиль", "2", "1", "00002"],
        ["3", "Студент Третий", "Тест", "Профиль", "2", "2", "00003"],
    ]
    service.redesign.apply_import(service.redesign.preview_import(headers, rows, 2026))
    groups = service.redesign.list_groups()
    discipline = service.save_discipline("Синтетическое оценивание")
    service.report_topic_catalogs = (
        ReportTopicCatalog(
            (discipline.name,),
            (
                ReportTopic("Алгоритмы и структуры", "Программирование", 12),
                ReportTopic("Анализ систем", "Моделирование", 25),
            ),
        ),
    )
    course = service.courses.save_course(
        discipline.id, groups[0].academic_year_id, 1, 2, "seminar", 1, "Курс", True
    )
    roster = service.courses.add_groups(course.id, {group.id for group in groups})
    service.courses.save_topic(course.id, 1, "Программа")
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    actions = GradingActions(service, CsvFiles())
    yield actions, course, lesson
    service.close()


def test_snapshot_and_commands_are_json_safe_and_report_ids_stable(grading):
    actions, course, lesson = grading
    snapshot = actions.snapshot(course.id, lesson.id)
    assert len(snapshot["reports"]) == len(snapshot["scoreRows"]) == len(snapshot["changes"]) == 3
    assert len(snapshot["criteria"]) == 18
    assert snapshot["reports"][0]["card"]["lesson_id"] == lesson.id
    assert snapshot["reports"][0]["student_id"] == snapshot["changes"][0]["student_id"]
    assert json.loads(json.dumps(snapshot, allow_nan=False))["course_id"] == course.id
    assert json.loads(canonical_json(snapshot))["course_id"] == course.id
    assert "grading.save_report" in MUTATING_COMMANDS
    assert "grading.fit" not in MUTATING_COMMANDS


def test_snapshot_indexes_reports_once_and_keeps_archived_occurrences_with_saved_cards(
    grading, monkeypatch
):
    actions, course, lesson = grading
    repository = actions.service.repository
    original_report_rows = repository.gr_reports
    course_ids = []

    def counted_reports(course_id):
        course_ids.append(course_id)
        return original_report_rows(course_id)

    monkeypatch.setattr(repository, "gr_reports", counted_reports)
    snapshot = actions.snapshot(course.id, lesson.id)
    assert course_ids == [course.id]
    assert len(snapshot["lessons"]) == 1
    [choice] = snapshot["lessons"]
    assert (choice["id"], choice["date"], choice["status"]) == (lesson.id, "2026-09-01", "planned")
    assert choice["time"] and choice["roster_id"] and choice["timezone"]
    assert all("lessons" not in row for row in snapshot["reports"])
    catalogs = StudentActions(actions.service).catalogs()
    [schedule_row] = CourseActions(actions.service).snapshot(course.id, lesson.id, catalogs)[
        "lessons"
    ]
    assert {key: schedule_row[key] for key in choice if key != "name"} == {
        key: value for key, value in choice.items() if key != "name"
    }

    card = dict(snapshot["reports"][0]["card"], topic="Сохранённая тема")
    actions.execute("grading.save_report", {"course_id": course.id, "card": card})
    block = actions.service.redesign.get_block(lesson.block_id)
    actions.service.lifecycle.delete_lesson(block.pair_ids[0], "Тестовое удаление")

    course_ids.clear()
    snapshot = actions.snapshot(course.id, lesson.id)
    assert course_ids == [course.id]
    assert snapshot["lessons"] == []
    saved, *others = snapshot["reports"]
    assert saved["lesson_id"] == lesson.id
    assert saved["archived_lesson"]["id"] == lesson.id
    assert saved["archived_lesson"]["status"] == "deleted"
    assert saved["lesson_starts_at"] == choice["starts_at"]
    assert all(
        row["archived_lesson"] is None and row["card"]["lesson_id"] is None for row in others
    )


def test_report_oral_written_toggle_preserves_all_levels_and_group_uniqueness(grading):
    actions, course, lesson = grading
    rows = actions.snapshot(course.id, lesson.id)["reports"]
    card = dict(
        rows[0]["card"],
        topic="Своя тема",
        levels=[0 if c.written else c.steps - 1 for c in CRITERIA],
    )
    payload = {"course_id": course.id, "card": card}
    assert actions.execute("grading.report_preview", payload)["report_preview"]["grade"] == 10
    actions.execute("grading.save_report", payload)
    written = actions.execute(
        "grading.report_preview", {"course_id": course.id, "card": dict(card, written=True)}
    )
    assert written["report_preview"]["grade"] < 7
    assert actions.snapshot(course.id)["reports"][0]["card"]["levels"] == card["levels"]
    second = dict(rows[1]["card"], topic=" СВОЯ ТЕМА ")
    with pytest.raises(AttendanceError, match="Тема уже выбрана"):
        actions.execute("grading.save_report", {"course_id": course.id, "card": second})
    assert actions.grading.report(course.id, second["student_id"]) is None
    with pytest.raises(AttendanceError, match="Выберите конкретное"):
        actions.execute(
            "grading.save_report", {"course_id": course.id, "card": dict(card, lesson_id=None)}
        )


def test_topic_search_reuses_tokens_sections_numbers_and_fuzzy_matching(grading):
    actions, course, _ = grading
    for query in ("алгортимы", "Программирование", "12"):
        result = actions.execute("grading.topic_search", {"course_id": course.id, "query": query})
        assert result["topic_matches"][0]["title"] == "Алгоритмы и структуры"
    assert actions.snapshot(course.id)["query"] == "12"


def test_rectangular_paste_uses_captured_order_zero_clear_and_atomic_validation(grading):
    actions, course, _ = grading
    for title in ("Первая", "Вторая"):
        actions.execute("grading.add_work", {"course_id": course.id, "title": title, "maximum": 20})
    snapshot = actions.snapshot(course.id)
    ids = list(reversed(snapshot["student_ids"]))
    works = [work["id"] for work in snapshot["works"]]
    payload = {
        "course_id": course.id,
        "student_ids": ids,
        "work_ids": works,
        "text": "5\t2:0\n\t20",
        "variant": "1",
    }
    actions.execute("grading.paste_scores", payload)
    assert actions.grading.scores(course.id) == {
        (ids[0], works[0]): 5,
        (ids[0], works[1]): 0,
        (ids[1], works[1]): 20,
    }
    assert actions.grading.variants(course.id) == {
        (ids[0], works[0]): "1",
        (ids[0], works[1]): "2",
        (ids[1], works[1]): "1",
    }
    before = actions.grading.scores(course.id)
    for invalid in ("10\t21", "1\t2\t3", "bad\t2"):
        with pytest.raises((AttendanceError, ValueError)):
            actions.execute("grading.paste_scores", dict(payload, text=invalid))
        assert actions.grading.scores(course.id) == before
    actions.execute(
        "grading.score",
        {"course_id": course.id, "student_id": ids[0], "work_id": works[0], "value": ""},
    )
    assert (ids[0], works[0]) not in actions.grading.scores(course.id)
    with pytest.raises(AttendanceError):
        actions.execute(
            "grading.score",
            {
                "course_id": course.id,
                "student_id": "other-course",
                "work_id": works[0],
                "value": "1",
            },
        )


def test_preview_fit_apply_and_templates_reuse_saved_baseline(grading):
    actions, course, lesson = grading
    snapshot = actions.snapshot(course.id, lesson.id)
    for row in snapshot["reports"]:
        actions.execute(
            "grading.save_report",
            {"course_id": course.id, "card": dict(row["card"], topic=row["full_name"])},
        )
    parameters = dict(snapshot["parameters"], neutral=9)
    payload = {"course_id": course.id, "parameters": parameters, "revision": 7}
    preview = actions.execute("grading.preview", payload)["preview"]
    assert preview["revision"] == 7
    assert preview["baseline_statistics"]["mean"] == 7
    assert preview["statistics"]["mean"] == 9
    assert actions.grading.parameters(course.id).neutral == 7
    fitted = actions.execute(
        "grading.fit",
        dict(
            payload,
            bounds=[{"key": "neutral", "minimum": 4, "maximum": 9}],
            target_mean=8,
            target_deviation=0,
        ),
    )["preview"]
    assert fitted["parameters"]["neutral"] == 8
    assert actions.snapshot(course.id)["edit_parameters"]["neutral"] == 8
    assert actions.grading.parameters(course.id).neutral == 7
    actions.execute("grading.save_template", dict(payload, name="Тестовый вариант"))
    loaded = actions.execute(
        "grading.load_template", {"course_id": course.id, "name": "Тестовый вариант"}
    )
    assert loaded["parameters"]["neutral"] == 9
    actions.execute("grading.apply", dict(payload, parameter_version=1))
    assert actions.grading.parameters(course.id).neutral == 9
    assert actions.grading.parameters(course.id).version == 2
    with pytest.raises(AttendanceError, match="расчёт изменился"):
        actions.execute("grading.apply", dict(payload, parameter_version=1))


def test_export_xlsx_and_unknown_commands(grading, tmp_path):
    actions, course, _ = grading
    path = tmp_path / "журнал с пробелами.xlsx"
    result = actions.execute("grading.export", {"course_id": course.id, "path": path.as_uri()})
    assert result["exported"] == str(path) and path.read_bytes().startswith(b"PK")
    for command in ("unknown", "grading.unknown"):
        with pytest.raises(AttendanceError):
            actions.execute(command, {"course_id": course.id})
    with pytest.raises(AttendanceError, match="локальный"):
        actions.execute(
            "grading.export", {"course_id": course.id, "path": "https://example.org/journal.xlsx"}
        )


def test_qml_written_score_requires_variant_and_supports_keyboard_clear(grading):
    from pathlib import Path

    from PySide6.QtCore import (
        Property,
        QCoreApplication,
        QEvent,
        QObject,
        QPoint,
        Qt,
        QUrl,
        Signal,
        Slot,
    )
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    actions, course, lesson = grading
    result = actions.execute("grading.add_work", {"course_id": course.id, "title": "Контрольная"})
    student_id = actions.snapshot(course.id)["student_ids"][0]
    work_id = result["work_id"]
    key = student_id, work_id
    # Match the schema used by existing scores before variants were introduced.
    with actions.service.repository.transaction():
        actions.service.repository._connection.execute(
            "INSERT INTO course_scores (work_id, student_id, score) VALUES (?, ?, ?)",
            (work_id, student_id, 54),
        )
    legacy = actions.snapshot(course.id, lesson.id)["scoreRows"][0]["cells"][0]
    assert legacy["legacy"] and legacy["variant"] is None

    class ScoreBackend(QObject):
        changed = Signal()
        cell = Property("QVariantMap", lambda self: self.current, notify=changed)

        def __init__(self):
            super().__init__()
            self.current = legacy
            self.submissions = []

        def write(self, score, variant):
            actions.execute(
                "grading.score",
                {
                    "course_id": course.id,
                    "student_id": student_id,
                    "work_id": work_id,
                    "value": score,
                    "variant": variant,
                },
            )
            self.current = actions.snapshot(course.id, lesson.id)["scoreRows"][0]["cells"][0]
            self.changed.emit()

        @Slot(str, str)
        def save(self, score, variant):
            self.submissions.append((score, variant))
            self.write(score, variant)

        @Slot()
        def clear(self):
            self.submissions.append((None, None))
            self.write(None, None)

    app = QApplication.instance() or QApplication([])
    backend = ScoreBackend()
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    component = QQmlComponent(engine)
    qml_path = Path(__file__).parents[1] / "src/attendance_lab/presentation/quick"
    component.setData(
        b"""import QtQuick
        WrittenScoreEntry {
            required property var backend
            width: 240; height: 40
            score: backend.cell.text
            variant: backend.cell.variant ?? ""
            legacy: backend.cell.legacy
            onSubmitted: (value, variant) => backend.save(value, variant)
            onClearRequested: backend.clear()
        }""",
        QUrl.fromLocalFile(str(qml_path / "WrittenScoreEntryTest.qml")),
    )
    entry = component.createWithInitialProperties({"backend": backend})
    assert entry is not None, [error.toString() for error in component.errors()]
    window = QQuickWindow()
    window.resize(320, 100)
    entry.setParentItem(window.contentItem())
    window.show()
    try:
        app.processEvents()
        score, variant = (
            child for child in entry.childItems() if child.property("placeholderText")
        )
        assert variant.property("placeholderText") == "Не указан"
        assert not variant.property("text")
        score.forceActiveFocus()
        score.selectAll()
        QTest.keyClick(window, Qt.Key.Key_6)
        QTest.keyClick(window, Qt.Key.Key_7)
        QTest.keyClick(window, Qt.Key.Key_Return)
        assert not backend.submissions
        assert variant.property("activeFocus")
        assert actions.grading.scores(course.id)[key] == 54
        assert key not in actions.grading.variants(course.id)

        QTest.keyClick(window, Qt.Key.Key_2)
        QTest.keyClick(window, Qt.Key.Key_Return)
        assert backend.submissions == [("67", "2")]
        assert actions.grading.scores(course.id)[key] == 67
        assert actions.grading.variants(course.id)[key] == "2"

        QTest.mouseDClick(window, Qt.MouseButton.LeftButton, pos=QPoint(30, 20))
        assert not score.property("readOnly")
        QTest.keyClick(window, Qt.Key.Key_Escape)
        score.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_F2)
        score.setProperty("cursorPosition", 2)
        QTest.keyClick(window, Qt.Key.Key_Backspace)
        assert score.property("text") == "6"
        QTest.keyClick(window, Qt.Key.Key_Home)
        QTest.keyClick(window, Qt.Key.Key_Delete)
        assert score.property("text") == ""
        assert backend.submissions == [("67", "2")]
        assert actions.grading.scores(course.id)[key] == 67
        QTest.keyClick(window, Qt.Key.Key_Escape)

        for shortcut, x in ((Qt.Key.Key_Delete, 30), (Qt.Key.Key_Backspace, 160)):
            backend.write("0", "0")
            assert actions.grading.scores(course.id)[key] == 0
            assert variant.property("text") == "0"
            QTest.mouseClick(
                window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 20)
            )
            QTest.keyClick(window, shortcut)
            assert backend.submissions[-1] == (None, None)
            assert key not in actions.grading.scores(course.id)
            assert key not in actions.grading.variants(course.id)
            assert not score.property("text") and not variant.property("text")

        assert not warnings, warnings
    finally:
        entry.deleteLater()
        window.close()
        window.deleteLater()
        engine.deleteLater()
        backend.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_qml_grading_page_loads_with_real_snapshot_and_external_models(grading):
    from pathlib import Path

    from PySide6.QtCore import (
        Property,
        QCoreApplication,
        QEvent,
        QObject,
        Qt,
        QUrl,
        Signal,
        Slot,
    )
    from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from attendance_lab.presentation.interface_zoom import InterfaceZoom
    from attendance_lab.presentation.quick.models import RecordListModel

    actions, course, lesson = grading
    actions.execute("grading.add_work", {"course_id": course.id, "title": "QML работа"})
    snapshot = actions.snapshot(course.id, lesson.id)

    class GradingTestScreen(QObject):
        changed = Signal()
        commandSucceeded = Signal(str, "QVariantMap")
        commandFailed = Signal(str, str)

        def __init__(self):
            super().__init__()
            self._drafts = {}
            self.calls = []
            self.errors = []
            self.defer = False
            self.queued = []
            self._snapshot = snapshot
            self.models = [RecordListModel(self) for _ in range(3)]
            for model, key in zip(self.models, ("reports", "scoreRows", "changes"), strict=True):
                model.set_rows(snapshot[key])

        state = Property("QVariantMap", lambda self: {"grading": self._snapshot}, notify=changed)
        drafts = Property("QVariantMap", lambda self: self._drafts, notify=changed)
        selectedCourseId = Property(int, lambda self: course.id, constant=True)
        selectedLessonId = Property(int, lambda self: lesson.id, constant=True)
        busy = Property(bool, lambda self: False, constant=True)
        gradingReportRows = Property(QObject, lambda self: self.models[0], constant=True)
        gradingWorkRows = Property(QObject, lambda self: self.models[1], constant=True)
        gradingChangeRows = Property(QObject, lambda self: self.models[2], constant=True)

        @Slot(str, str, "QVariant")
        def setDraft(self, scope, key, value):
            if hasattr(value, "toVariant"):
                value = value.toVariant()
            self._drafts.setdefault(scope, {})[key] = value
            self.changed.emit()

        @Slot(str, "QVariantMap")
        def command(self, name, payload):
            self.calls.append((name, payload))
            if self.defer:
                self.queued.append((name, payload))
                return
            try:
                result = actions.execute(name, payload)
                self._snapshot = actions.snapshot(course.id, lesson.id)
                for model, key in zip(
                    self.models, ("reports", "scoreRows", "changes"), strict=True
                ):
                    model.set_rows(self._snapshot[key])
                self.changed.emit()
                self.commandSucceeded.emit(name, result)
            except (AttendanceError, ValueError) as error:
                self.errors.append(str(error))
                self.commandFailed.emit(name, str(error))

    from attendance_lab.presentation.quick.theme import apply_light_theme

    app = QApplication.instance() or QApplication([])
    apply_light_theme(app)
    screen = GradingTestScreen()
    zoom = InterfaceZoom(screen)
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(
            str(Path(__file__).parents[1] / "src/attendance_lab/presentation/quick/GradingPage.qml")
        ),
    )
    assert not component.errors(), [error.toString() for error in component.errors()]
    page = component.createWithInitialProperties({"app": screen, "interfaceZoom": zoom})
    assert page is not None, [error.toString() for error in component.errors()]
    page.setProperty("width", 1200)
    page.setProperty("height", 900)
    window = QQuickWindow()
    window.resize(1200, 900)
    page.setParentItem(window.contentItem())
    window.show()
    try:
        app.processEvents()

        def invoke(scope, text):
            expression = QQmlExpression(engine.rootContext(), scope, text, engine)
            expression.evaluate()
            assert not expression.hasError(), expression.error().toString()
            app.processEvents()

        def value_of(obj, name):
            value = obj.property(name)
            return value.toVariant() if hasattr(value, "toVariant") else value

        def refresh():
            screen._snapshot = actions.snapshot(course.id, lesson.id)
            screen.changed.emit()
            app.processEvents()

        assert page.findChild(QObject, "gradingReports") is not None
        assert page.findChild(QObject, "gradingScores") is not None
        assert page.findChild(QObject, "gradingChanges") is not None
        expression = QQmlExpression(
            engine.rootContext(), page, "beginReport(app.state.grading.reports[0])"
        )
        expression.evaluate()
        assert not expression.hasError(), expression.error().toString()
        app.processEvents()
        dialog = page.findChild(QObject, "gradingReportDialog")
        assert dialog.property("visible")
        assert dialog.transientParent() is None
        window.requestActivate()
        app.processEvents()
        assert dialog.isVisible()
        dialog.resize(420, 340)
        dialog.requestActivate()
        app.processEvents()
        assert screen.calls[-1][0] == "grading.report_preview"
        assert page.property("criterionDefinitions").toVariant(), "rubric definitions missing"
        QTest.keyClick(dialog, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert zoom.percent == 110 and dialog.property("interfaceScale") == 1.1
        QTest.keyClick(dialog, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
        assert zoom.percent == 100

        def visual_item(root, name):
            if root.objectName() == name:
                return root
            for child in root.childItems():
                found = visual_item(child, name)
                if found is not None:
                    return found
            return None

        assert visual_item(dialog.contentItem(), "rubricCriterion0").property("enabled") is False
        oral = visual_item(dialog.contentItem(), "rubricCriterion13")
        assert oral.property("enabled") is True
        expression = QQmlExpression(engine.rootContext(), oral, "forceActiveFocus()")
        expression.evaluate()
        QTest.keyClick(dialog, Qt.Key.Key_Right)
        app.processEvents()
        draft_key = f"{course.id}:report:{snapshot['student_ids'][0]}"
        assert screen._drafts["grading"][draft_key]["levels"][13] == 3
        expression = QQmlExpression(engine.rootContext(), dialog, "save()")
        expression.evaluate()
        assert dialog.property("visible") and screen.errors
        assert screen._drafts["grading"][draft_key]["topic"] == ""
        # A poll may carry a successful older read after the GUI draft has changed.
        invoke(dialog, 'setField("topic", "Алгоритмы")')
        report_revision = dialog.property("revision")
        assert value_of(dialog, "previewResult") == {}
        search = {
            "course_id": course.id,
            "student_id": snapshot["student_ids"][0],
            "query": "Алгоритмы",
            "revision": report_revision - 1,
        }
        actions.execute("grading.topic_search", search)
        refresh()
        assert not dialog.property("topicMatchesCurrent")
        actions.execute(
            "grading.topic_search", dict(search, revision=report_revision, student_id="other")
        )
        refresh()
        assert not dialog.property("topicMatchesCurrent")
        actions.execute("grading.topic_search", dict(search, revision=report_revision))
        refresh()
        assert dialog.property("topicMatchesCurrent")
        current_card = screen._drafts["grading"][draft_key]
        actions.execute(
            "grading.report_preview",
            {"course_id": course.id, "card": current_card, "revision": report_revision - 1},
        )
        refresh()
        assert value_of(dialog, "previewResult") == {}
        actions.execute(
            "grading.report_preview",
            {"course_id": course.id, "card": current_card, "revision": report_revision},
        )
        refresh()
        assert value_of(dialog, "previewResult")["revision"] == report_revision
        expression = QQmlExpression(
            engine.rootContext(), dialog, 'setField("topic", "Сохранённая QML тема")'
        )
        expression.evaluate()
        expression = QQmlExpression(engine.rootContext(), dialog, "save()")
        expression.evaluate()
        app.processEvents()
        assert not expression.hasError(), expression.error().toString()
        assert not dialog.property("visible"), (screen.calls, screen.errors)
        assert (
            actions.grading.report(course.id, snapshot["student_ids"][0]).topic
            == "Сохранённая QML тема"
        )
        screen.defer = True
        invoke(page, 'setParameter("neutral", 9)')
        invoke(page, "preview()")
        old_request = screen.queued[-1]
        invoke(page, 'setCoefficient(0, "value", 2)')
        invoke(page, 'setParameter("neutral", 8)')
        assert value_of(page, "projection") == {}
        actions.execute(*old_request)
        refresh()
        assert value_of(page, "projection") == {}
        parameter_key = f"{course.id}:parameters"
        revision_key = f"{course.id}:revision"
        current_revision = screen._drafts["grading"][revision_key]
        # A stale fit result must not overwrite edits made while it was running.
        screen._snapshot.update(
            edit_token=100,
            edit_revision=current_revision - 1,
            edit_kind="fit",
            edit_parameters=dict(snapshot["parameters"], neutral=9),
        )
        screen.changed.emit()
        app.processEvents()
        assert screen._drafts["grading"][parameter_key]["neutral"] == 8
        fitted = dict(screen._drafts["grading"][parameter_key], neutral=7)
        actions.execute(
            "grading.preview",
            {"course_id": course.id, "parameters": fitted, "revision": current_revision},
        )
        refresh()
        screen._snapshot.update(
            edit_token=101, edit_revision=current_revision, edit_kind="fit", edit_parameters=fitted
        )
        screen.changed.emit()
        app.processEvents()
        assert screen._drafts["grading"][parameter_key]["neutral"] == 7
        assert value_of(page, "projection")["revision"] == current_revision
        actions.execute(
            "grading.save_template",
            {"course_id": course.id, "name": "QML вариант", "parameters": dict(fitted, neutral=9)},
        )
        actions.execute(
            "grading.load_template",
            {"course_id": course.id, "name": "QML вариант", "revision": current_revision},
        )
        refresh()
        assert screen._drafts["grading"][parameter_key]["neutral"] == 9
        assert screen._drafts["grading"][revision_key] > current_revision
        assert value_of(page, "projection") == {}
        assert not warnings, warnings
    finally:
        page.deleteLater()
        window.close()
        window.deleteLater()
        engine.deleteLater()
        screen.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
