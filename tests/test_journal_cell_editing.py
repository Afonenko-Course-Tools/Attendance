"""Direct cell commands preserve the viewed page and active registration."""

from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_course_observed_students import lab, scan  # noqa: F401
from test_course_workflow import course  # noqa: F401

from attendance_lab.application.controller import AttendanceController
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.presentation.admin import MainWindow
from attendance_lab.presentation.preparation import FormDialog


def journal_window(service, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        service,
        AttendanceController(service, DemoReader),
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "synthetic-cell-editing.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    window.tabs.setCurrentWidget(window.course_journal)
    window.show()
    window.activateWindow()
    app.processEvents()
    return app, window


@pytest.mark.parametrize("action", ["button", "space", "enter", "double_click"])
def test_cell_marks_whole_lesson_without_dialog_or_switching_registration(
    course,  # noqa: F811
    tmp_path,
    monkeypatch,
    action,
):
    service, selected_course, roster = course
    target = service.courses.schedule(selected_course.id, roster.id, 1, date(2026, 9, 1), 1)
    other = service.courses.schedule(selected_course.id, roster.id, 2, date(2026, 9, 8), 1)
    target_block = service.redesign.get_block(target.block_id)
    active_block = service.redesign.get_block(other.block_id)
    app, window = journal_window(service, tmp_path)
    try:
        window.controller.open_session(active_block.id, set(active_block.pair_ids))
        session_id = window.controller.active_session_id

        def unexpected(*args, **kwargs):
            pytest.fail("Однозначная отметка из ячейки не требует диалога или перехода на занятие")

        monkeypatch.setattr(FormDialog, "exec", unexpected)
        monkeypatch.setattr(window.workspace, "select_lesson", unexpected)
        pane = window.course_workspace
        pane.journal.setCurrentCell(0, 5)
        student_id = pane.journal.item(0, 5).data(Qt.ItemDataRole.UserRole)[0]
        pane.journal.setFocus()
        app.processEvents()
        assert "Пропуск" in pane.journal.item(0, 5).toolTip()
        assert not service.redesign.list_manual_batches(), "Выделение само по себе не пишет данные"
        if action == "button":
            assert pane.journal_reason.currentText() == "был без пропуска"
            assert not pane.journal_reason.isEditable()
            pane.journal_reason.setCurrentIndex(1)
            pane.tools["mark"].trigger()
        elif action == "double_click":
            rectangle = pane.journal.visualItemRect(pane.journal.item(0, 5))
            QTest.mouseClick(
                pane.journal.viewport(), Qt.MouseButton.LeftButton, pos=rectangle.center()
            )
            QTest.mouseDClick(
                pane.journal.viewport(), Qt.MouseButton.LeftButton, pos=rectangle.center()
            )
        else:
            QTest.keyClick(
                pane.journal,
                Qt.Key.Key_Space if action == "space" else Qt.Key.Key_Return,
            )
        app.processEvents()
        assert len(service.redesign.list_manual_batches()) == 1
        assert service.redesign.list_manual_batches()[0].reason == (
            "отработал с другой группой" if action == "button" else "был без пропуска"
        )
        for pair_id in target_block.pair_ids:
            assert {
                evidence.student_id
                for evidence in service.redesign.pair_evidence(pair_id)
                if evidence.active and evidence.kind == "manual"
            } == {student_id}
        assert window.tabs.currentWidget() is window.course_journal
        assert window.controller.active_session_id == session_id
        assert service.courses.attendance(selected_course.id, student_id, target.topic_id)
        assert not service.courses.attendance(selected_course.id, student_id, other.topic_id)
        assert pane.journal.item(0, 5).text() == "✓"
        assert "Посещение подтверждено" in pane.journal.item(0, 5).toolTip()
        assert "Пропуск" not in pane.journal.item(0, 5).toolTip()
        assert not pane.tools["mark"].isEnabled()
        pane.mark_rectangle()
        assert len(service.redesign.list_manual_batches()) == 1
    finally:
        window.close()


def test_observed_fallback_cell_can_be_marked_without_enrolling_student(
    lab,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    service, students, selected_course, block, _ = lab
    scan(service, block, "0000E901", students[0].student_id)
    service.courses.save_topic(selected_course.id, 2, "Вторая лабораторная")
    target = service.courses.schedule(selected_course.id, block.roster_id, 2, date(2026, 9, 8), 1)
    _, window = journal_window(service, tmp_path)
    try:

        def unexpected(*args):
            pytest.fail("Единственное занятие должно отмечаться прямо из таблицы")

        monkeypatch.setattr(FormDialog, "exec", unexpected)
        pane = window.course_workspace
        assert pane.journal_observed_only and pane.journal.rowCount() == 1
        pane.journal.setCurrentCell(0, 6)
        assert pane.tools["mark"].isEnabled()
        pane.tools["mark"].trigger()
        assert pane.journal.item(0, 6).text() == "✓"
        assert service.courses.attendance(
            selected_course.id, students[0].student_id, target.topic_id
        )
        assert pane.journal_observed_only
        assert pane.journal.item(0, pane.journal.columnCount() - 1).text() == "—"
        assert service.courses.list_students(selected_course.id) == []
        assert service.grading.students(selected_course.id) == ()
        assert window.tabs.currentWidget() is window.course_journal
    finally:
        window.close()
