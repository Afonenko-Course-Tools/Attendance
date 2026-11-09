"""Unassigned courses retain their evidence table without inventing enrollment."""

from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from test_course_observed_students import lab, scan  # noqa: F401
from test_course_workspace_toolbar import LessonStub

from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.presentation.course_workspace import CourseWorkspace


@pytest.fixture
def pane(lab):  # noqa: F811
    service, students, course, block, empty_course = lab
    app = QApplication.instance() or QApplication([])
    lesson = LessonStub()
    widget = CourseWorkspace(service, lambda: None, lesson, QWidget(), files=CsvFiles())
    widget.refresh()
    widget.tabs.setCurrentWidget(widget.journal_pane)
    widget.show()
    app.processEvents()
    yield widget, service, students, course, block, empty_course
    widget.close()
    lesson.close()
    app.processEvents()


def row_ids(pane):
    return {
        pane.journal.item(row, 0).data(Qt.ItemDataRole.UserRole)
        for row in range(pane.journal.rowCount())
    }


def test_unassigned_course_shows_identified_visitors_and_can_export_them(
    pane, tmp_path, monkeypatch
):
    widget, service, students, course, block, empty_course = pane
    _, other_block = empty_course(2)
    scan(service, block, "0000A001", students[0].student_id)
    scan(service, block, "0000A002")
    scan(service, other_block, "0000A003", students[1].student_id)
    widget.refresh_journal()
    assert widget.journal_observed_only
    assert row_ids(widget) == {students[0].student_id}
    assert widget.journal.item(0, 5).text() == "✓"
    assert widget.journal.item(0, widget.journal.columnCount() - 1).text() == "—"
    assert "сохранёнными посещениями" in widget.journal_notice.text()
    assert widget.journal_notice.isVisibleTo(widget.journal_pane)
    assert not widget.journal_status.isEnabled()
    assert widget.tools["export"].isEnabled()
    path = tmp_path / "observed-journal.xlsx"
    monkeypatch.setattr(
        "attendance_lab.presentation.grading.QFileDialog.getSaveFileName",
        lambda *args: (str(path), "Excel (*.xlsx)"),
    )
    widget.tools["export"].trigger()
    with ZipFile(path) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        assert students[0].full_name in sheet and students[1].full_name not in sheet
        assert "0001" in sheet
    assert service.courses.list_students(course.id) == []
    assert service.grading.students(course.id) == ()


def test_late_binding_adds_and_removes_rows_without_rescan_or_resetting_grade_draft(
    pane, monkeypatch
):
    widget, service, students, course, block, _ = pane
    uid = "0000B001"
    scan(service, block, uid)
    widget.update_attendance(set(block.pair_ids))
    assert widget.journal.rowCount() == 0

    def unexpected_reset(*args, **kwargs):
        pytest.fail("Обновление строк посещений не должно сбрасывать черновик оценивания")

    monkeypatch.setattr(widget.grading, "set_context", unexpected_reset)
    service.bind_card(uid, students[0].student_id)
    widget.update_attendance(set(block.pair_ids))
    assert row_ids(widget) == {students[0].student_id}
    assert widget.journal.item(0, 5).text() == "✓"
    service.bind_card(
        uid, students[1].student_id, correction=True, expected_student_id=students[0].student_id
    )
    widget.update_attendance()
    assert row_ids(widget) == {students[1].student_id}
    service.unbind_card(uid, expected_student_id=students[1].student_id)
    widget.update_attendance()
    assert widget.journal.rowCount() == 0
    assert len(service.repository.rd_pair_evidence(block.pair_ids[0])) == 1
    assert service.courses.list_students(course.id) == []


def test_explicit_membership_takes_priority_and_enables_absence_filter(pane):
    widget, service, students, course, block, _ = pane
    scan(service, block, "0000C001", students[2].student_id)
    widget.update_attendance()
    assert row_ids(widget) == {students[2].student_id}
    service.courses.assign_groups(course.id, block.roster_id, {students[0].group_id})
    widget.update_attendance()
    assert not widget.journal_observed_only
    assert row_ids(widget) == {students[0].student_id, students[1].student_id}
    assert widget.journal_status.isEnabled()
    assert all(
        widget.journal.item(row, widget.journal.columnCount() - 1).text() == "1"
        for row in range(widget.journal.rowCount())
    )
