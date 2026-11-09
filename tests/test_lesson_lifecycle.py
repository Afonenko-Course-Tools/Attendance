"""Synthetic reversible lesson removal preserves original attendance and identities."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import test_course_workflow as course_tests

from attendance_lab.domain.models import AttendanceError


@pytest.fixture
def course(tmp_path):
    yield from course_tests.course.__wrapped__(tmp_path)


def test_whole_block_delete_restore_preserves_facts_and_projection(course):
    service, semester_course, roster = course
    occurrence = service.courses.schedule(semester_course.id, roster.id, 1, date(2026, 9, 7), 1)
    block = service.redesign.get_block(occurrence.block_id)
    student = service.courses.list_students(semester_course.id)[0].student_id
    service.bind_card("001122", student)
    session = service.courses.open_registration(occurrence.id)
    service.redesign.record_session_scan(
        session.id, "001122", datetime(2026, 9, 7, 6, 3, tzinfo=UTC)
    )
    service.redesign.close_session(session.id)
    service.redesign.create_manual_batch(
        {(student, pair) for pair in block.pair_ids}, "Синтетическая отметка"
    )
    before_lessons = [service.get_lesson(pair) for pair in block.pair_ids]
    before_evidence = {pair: service.redesign.pair_evidence(pair) for pair in block.pair_ids}
    before_scans = service.repository.list_scans()
    deletion = service.lifecycle.delete_lesson(block.pair_ids[-1], "Фиктивное занятие для тестов")
    assert set(deletion.lesson_ids) == set(block.pair_ids)
    assert not service.courses.list_lessons(semester_course.id)
    assert not service.redesign.list_blocks()
    assert not service.list_lessons()
    assert service.repository.list_scans() == before_scans
    assert [service.get_lesson(pair) for pair in block.pair_ids] == before_lessons
    assert {
        pair: service.redesign.pair_evidence(pair) for pair in block.pair_ids
    } == before_evidence
    with pytest.raises(AttendanceError):
        service.open_lesson(block.pair_ids[0])
    with pytest.raises(AttendanceError):
        service.redesign.open_session(block.id, set(block.pair_ids))
    service.lifecycle.restore_deletion(deletion.id)
    assert service.courses.list_lessons(semester_course.id) == [occurrence]
    assert [service.get_lesson(pair) for pair in block.pair_ids] == before_lessons
    assert service.courses.attendance(semester_course.id, student, occurrence.topic_id)
    assert service.lifecycle.list_deletions() == []
    assert service.lifecycle.list_deletions(include_restored=True)[0].restored_at is not None
    with pytest.raises(AttendanceError):
        service.lifecycle.restore_deletion(deletion.id)


def test_active_registration_rejects_whole_block_atomically(course):
    service, semester_course, roster = course
    occurrence = service.courses.schedule(
        semester_course.id, roster.id, 1, datetime.now(ZoneInfo("Europe/Minsk")).date(), 1
    )
    block = service.redesign.get_block(occurrence.block_id)
    service.redesign.open_session(block.id, {block.pair_ids[0]})
    with pytest.raises(AttendanceError, match="регистрац"):
        service.lifecycle.delete_lesson(block.pair_ids[-1], "Нельзя удалять активное")
    assert service.lifecycle.deleted_ids() == set()
    assert service.lifecycle.list_deletions(include_restored=True) == []


def test_legacy_schedule_deletes_exact_selected_id_after_sort(course, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog

    from attendance_lab.application.controller import AttendanceController
    from attendance_lab.infrastructure.csv_io import CsvFiles
    from attendance_lab.infrastructure.readers import DemoReader
    from attendance_lab.presentation.admin import MainWindow
    from attendance_lab.presentation.preparation import FormDialog

    application = QApplication.instance() or QApplication([])
    service, semester_course, _ = course
    first = service.save_lesson(
        semester_course.discipline_id,
        datetime(2026, 9, 7, 20, 49, tzinfo=UTC),
        datetime(2026, 9, 7, 22, 19, tzinfo=UTC),
        "Europe/Minsk",
        title="Фиктивное занятие",
    )
    second = service.save_lesson(
        semester_course.discipline_id,
        first.starts_at + timedelta(days=1),
        first.ends_at + timedelta(days=1),
        "Europe/Minsk",
        title="Оставить занятие",
    )
    window = MainWindow(
        service,
        AttendanceController(service, DemoReader),
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "synthetic-course.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    monkeypatch.setattr(FormDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    try:
        window.schedule.lessons.sortItems(0, Qt.SortOrder.DescendingOrder)
        row = next(
            row
            for row in range(window.schedule.lessons.rowCount())
            if window.schedule.lessons.item(row, 0).data(Qt.ItemDataRole.UserRole) == first.id
        )
        window.schedule.lessons.selectRow(row)
        window.schedule.delete_selected()
        assert service.lifecycle.deleted_ids() == {first.id}
        assert [lesson.id for lesson in service.list_lessons()] == [second.id]
        service.lifecycle.restore_deletion(service.lifecycle.list_deletions()[0].id)
        assert service.get_lesson(first.id).starts_at == first.starts_at
    finally:
        window.close()
        application.processEvents()


def test_source_schedule_projection_excludes_course_bound_pairs(course):
    service, semester_course, roster = course
    service.courses.schedule(
        semester_course.id, roster.id, 1, datetime.now(ZoneInfo("Europe/Minsk")).date(), 1
    )
    start = datetime(2026, 9, 7, 20, 49, tzinfo=UTC)
    source = service.save_lesson(
        semester_course.discipline_id,
        start,
        start + timedelta(minutes=90),
        "Europe/Minsk",
        "Фиктивный исходный пример",
    )
    assert [lesson.id for lesson in service.list_source_lessons(semester_course.discipline_id)] == [
        source.id
    ]
