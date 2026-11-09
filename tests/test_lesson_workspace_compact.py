"""Compact lesson controls retain stable targets and historical lecture rosters."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.redesign import TeachingRoster
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.lesson_workspace import LessonWorkspace


@pytest.fixture
def lecture(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = AttendanceService(
        SQLiteRepository(tmp_path / "synthetic-lecture.sqlite3", mode="demo")
    )
    service.redesign.apply_import(
        service.redesign.preview_import(
            [
                "№",
                "ФИО",
                "Специальность",
                "Специализация/профилизация",
                "Курс",
                "Группа",
                "№ зачётки",
            ],
            [
                ["1", "Вымышленный Алексей", "Тест", "Профиль", "2", "1", "0001"],
                ["2", "Вымышленный Борис", "Тест", "Профиль", "2", "1", "0002"],
            ],
            datetime.now(ZoneInfo("Europe/Minsk")).date().year,
        )
    )
    discipline = service.save_discipline("Учебные лекции")
    year = service.redesign.list_years()[0]
    course = service.courses.save_course(
        discipline.id, year.id, 1, 2, "lecture", 1, "Учебный поток"
    )
    students = {s.id for s in service.list_students()}
    rosters = []
    for i, ids in enumerate((students, set()), 1):
        with service.repository.transaction():
            roster = service.repository.rd_save_roster(
                TeachingRoster(
                    0,
                    discipline.id,
                    year.id,
                    1,
                    f"Исторический состав занятия {i}",
                    "lecture",
                    1,
                    "students",
                    (),
                    tuple(ids),
                )
            )
        service.courses.add_roster(course.id, roster.id)
        rosters.append(roster)
    service.courses.save_topic(course.id, 1, "Введение в предмет")
    occurrence = service.courses.schedule(
        course.id,
        rosters[0].id,
        1,
        datetime.now(ZoneInfo("Europe/Minsk")).date() - timedelta(days=1),
        1,
    )
    block = service.redesign.get_block(occurrence.block_id)
    controller = AttendanceController(service, DemoReader)
    widget = LessonWorkspace(service, controller, lambda: widget.refresh())
    widget.selected_course_id = course.id
    widget.today.setChecked(False)
    widget.refresh()
    widget.resize(980, 650)
    widget.show()
    app.processEvents()
    yield widget, service, controller, course, block
    widget.close()
    controller.shutdown()
    service.close()
    app.processEvents()


def test_lecture_uses_natural_lesson_and_stream_without_rewriting_history(lecture):
    widget, service, _, course, block = lecture
    repository = service.repository
    tables = ("teaching_rosters", "lesson_roster", "lesson_context")
    before = {
        table: repository._connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in tables
    }
    label = widget.blocks.currentText()
    assert "09:00–10:20" in label and "1. Введение в предмет" in label
    assert "Исторический состав" not in label and "пара " not in label and "#" not in label
    assert widget.scope.text() == "Поток · посещение за занятие"
    widget.show_context.setChecked(True)
    assert widget.table.isColumnHidden(6)
    assert widget.model.headerData(7, Qt.Orientation.Horizontal) == "Посещение занятия"
    widget.refresh()
    assert len(service.courses.list_rosters(course.id)) == 2
    assert {
        table: repository._connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in tables
    } == before
    assert set(widget.viewed_pair_ids()) == set(block.pair_ids)


def test_buttons_follow_actual_selection_and_available_evidence(lecture):
    widget, service, _, _, block = lecture
    assert widget.open_button.isEnabled() and not widget.manual_button.isEnabled()
    assert not widget.stop_button.isEnabled()
    assert not widget.manual_selected_button.isEnabled()
    assert not widget.evidence_button.isEnabled()
    assert not widget.correct_button.isEnabled()
    assert not widget.confirm_button.isEnabled()
    widget.table.selectRow(0)
    selected = widget.selected_ids()
    assert widget.manual_button.isEnabled()
    assert widget.manual_selected_button.isEnabled()
    assert not widget.evidence_button.isEnabled()
    service.redesign.create_manual_batch(
        {(student, block.pair_ids[0]) for student in selected}, "Синтетическое подтверждение"
    )
    widget.update_counts()
    assert widget.selected_ids() == selected
    assert widget.evidence_button.isEnabled()
    widget.table.clearSelection()
    assert not widget.manual_button.isEnabled()
    assert not widget.manual_selected_button.isEnabled() and not widget.evidence_button.isEnabled()


def test_manual_toolbar_requires_explicit_student_and_saves_that_selection(lecture, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    widget, service, _, _, block = lecture
    prompts = []

    def reason(*args):
        prompts.append(args[2])
        return "Подтверждение преподавателя", True

    monkeypatch.setattr(QInputDialog, "getText", reason)
    widget.manual_button.click()
    widget.manual_block()
    assert not prompts and not service.redesign.list_manual_batches()
    widget.table.selectRow(0)
    selected = widget.selected_ids()
    widget.manual_button.click()
    assert len(prompts) == 1
    assert {
        evidence.student_id
        for evidence in service.redesign.pair_evidence(block.pair_ids[0])
        if evidence.kind == "manual" and evidence.active
    } == selected


def test_explicit_search_result_can_be_marked_without_a_card(lecture, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    widget, service, _, _, block = lecture
    widget.open_search()
    widget.course.setCurrentIndex(widget.course.findData(2))
    widget.search.setText("Алексей")
    assert not widget.manual_button.isEnabled()
    widget.matches.setCurrentRow(0)
    selected = widget.manual_target_ids()
    assert len(selected) == 1 and widget.manual_button.isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Без карты", True))
    widget.manual_button.click()
    assert {
        evidence.student_id
        for evidence in service.redesign.pair_evidence(block.pair_ids[0])
        if evidence.kind == "manual" and evidence.active
    } == selected
    widget.matches.setCurrentRow(-1)
    assert not widget.manual_button.isEnabled()


@pytest.mark.parametrize("button", ["manual_button", "manual_selected_button"])
def test_table_selection_overrides_an_older_search_candidate(lecture, monkeypatch, button):
    from PySide6.QtWidgets import QInputDialog

    widget, service, _, _, block = lecture
    widget.open_search()
    widget.course.setCurrentIndex(widget.course.findData(2))
    widget.search.setText("Алексей")
    widget.matches.setCurrentRow(0)
    search_ids = widget.manual_target_ids()
    for row in range(widget.proxy.rowCount()):
        index = widget.proxy.index(row, 0)
        if index.data(Qt.ItemDataRole.UserRole) not in search_ids:
            widget.table.selectRow(row)
            break
    table_ids = widget.selected_ids()
    assert table_ids and table_ids != search_ids
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Выбран в таблице", True))
    getattr(widget, button).click()
    assert {
        evidence.student_id
        for evidence in service.redesign.pair_evidence(block.pair_ids[0])
        if evidence.kind == "manual" and evidence.active
    } == table_ids


def test_registration_toolbar_and_manual_search_use_real_targets(lecture):
    widget, _, controller, _, block = lecture
    widget.open_button.click()
    assert controller.active_session_id is not None
    assert not widget.open_button.isEnabled() and widget.stop_button.isEnabled()
    widget.open_selected()
    assert tuple(widget.viewed_pair_ids()) == block.pair_ids
    widget.stop_button.click()
    assert controller.active_session_id is None
    assert widget.open_button.isEnabled() and not widget.stop_button.isEnabled()
    widget.open_search()
    widget.course.setCurrentIndex(widget.course.findData(2))
    widget.search.setText("Алексей")
    widget.matches.setCurrentRow(0)
    assert not widget.confirm_button.isEnabled(), (
        "Поиск без карты сам по себе не является действием привязки"
    )
    widget.start_manual()
    widget.search.setText("Алексей")
    assert not widget.confirm_button.isEnabled()
    widget.matches.setCurrentRow(0)
    assert widget.confirm_button.isEnabled()
    widget.search.setText("Несуществующее имя")
    assert not widget.confirm_button.isEnabled()
    widget.defer()
    assert not widget.manual_mode


def test_no_lesson_keeps_unavailable_commands_disabled(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = AttendanceService(SQLiteRepository(tmp_path / "empty.sqlite3", mode="demo"))
    controller = AttendanceController(service, DemoReader)
    widget = LessonWorkspace(service, controller, lambda: None)
    try:
        widget.refresh()
        for button in (
            widget.open_button,
            widget.stop_button,
            widget.manual_button,
            widget.manual_selected_button,
            widget.evidence_button,
            widget.correct_button,
            widget.confirm_button,
            widget.search_button,
        ):
            assert not button.isEnabled()
        assert app is not None
    finally:
        widget.close()
        controller.shutdown()
        service.close()


def test_future_closed_manual_actions_require_registration_and_recheck_draft(lecture, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    widget, service, controller, course, past = lecture
    service.courses.save_topic(course.id, 2, "Будущее занятие")
    occurrence = service.courses.schedule(
        course.id,
        past.roster_id,
        2,
        datetime.now(ZoneInfo("Europe/Minsk")).date() + timedelta(days=1),
        1,
    )
    future = service.redesign.get_block(occurrence.block_id)
    widget.refresh()
    widget.select_lesson(future.pair_ids[0])
    widget.table.selectRow(0)
    assert not widget.manual_button.isEnabled()
    assert not widget.manual_selected_button.isEnabled()
    assert not widget.manual_allowed((*past.pair_ids, *future.pair_ids))

    def unexpected_dialog(*args):
        pytest.fail("Будущее закрытое занятие не должно открывать форму сохранения отметки")

    monkeypatch.setattr(QInputDialog, "getText", unexpected_dialog)
    widget.start_manual()
    widget.manual_block()
    assert not widget.manual_mode and not service.redesign.list_manual_batches()
    widget.open_button.click()
    widget.table.selectRow(0)
    assert controller.active_session_id is not None
    assert widget.manual_button.isEnabled() and widget.manual_selected_button.isEnabled()
    assert widget.manual_allowed((*past.pair_ids, *future.pair_ids))
    widget.start_manual()
    widget.search.setText("Алексей")
    widget.matches.setCurrentRow(0)
    assert widget.manual_mode and widget.confirm_button.isEnabled()
    widget.stop_button.click()
    assert not widget.manual_button.isEnabled() and not widget.manual_selected_button.isEnabled()
    assert not widget.confirm_button.isEnabled()
    widget.matches.setCurrentRow(0)
    widget.confirm_match()
    assert not service.redesign.list_manual_batches()
