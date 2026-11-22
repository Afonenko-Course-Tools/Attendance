"""Programme edits and occurrence correction preserve original attendance facts."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
import test_course_workflow as course_tests

from attendance_lab.domain.grading import ReportCard
from attendance_lab.domain.models import AttendanceError


@pytest.fixture
def course(tmp_path):
    yield from course_tests.course.__wrapped__(tmp_path)


def test_programme_can_extend_past_eight_without_creating_any_lessons(course):
    service, semester, _ = course
    before = service.courses.list_topics(semester.id)
    service.courses.add_topics(semester.id, 5, 7)
    topics = service.courses.list_topics(semester.id)
    assert [topic.number for topic in topics] == list(range(1, 12))
    assert topics[:4] == before
    service.courses.save_topic(semester.id, 9, "Девятая тема Java")
    assert service.courses.list_topics(semester.id)[8].title == "Девятая тема Java"
    assert service.courses.list_lessons(semester.id) == []
    assert service.redesign.list_blocks() == []


def test_new_topic_duplicate_is_not_an_implicit_rename_and_bulk_add_rolls_back(course):
    service, semester, _ = course
    with pytest.raises(AttendanceError, match="уже есть"):
        service.courses.add_topic(semester.id, 2, "Случайное переименование")
    service.courses.add_topic(semester.id, 7, "Седьмая исходная тема")
    before = service.courses.list_topics(semester.id)
    with pytest.raises(AttendanceError):
        service.courses.add_topics(semester.id, 5, 4)
    assert service.courses.list_topics(semester.id) == before


def test_change_one_occurrence_topic_preserves_ids_times_scan_manual_and_saved_report(course):
    service, semester, roster = course
    first = service.courses.schedule(semester.id, roster.id, 1, date(2026, 9, 1), 1)
    other = service.courses.schedule(semester.id, roster.id, 1, date(2026, 9, 8), 3)
    first_block = service.redesign.get_block(first.block_id)
    students = service.courses.list_students(semester.id)
    service.bind_card("0011AB", students[0].student_id)
    session = service.courses.open_registration(first.id)
    service.redesign.record_session_scan(
        session.id, "0011AB", datetime(2026, 9, 1, 6, 1, tzinfo=UTC)
    )
    service.redesign.close_session(session.id)
    service.redesign.create_manual_batch(
        {(students[1].student_id, pair) for pair in first_block.pair_ids}, "Исходная ручная отметка"
    )
    service.courses.save_course(
        semester.discipline_id,
        semester.academic_year_id,
        semester.semester,
        semester.study_course,
        semester.kind,
        semester.duration,
        semester.name,
        True,
        semester.id,
    )
    report = ReportCard(semester.id, students[0].student_id, first.id, "Сохранённый доклад")
    service.grading.save_report(report)
    evidence = {pair: service.redesign.pair_evidence(pair) for pair in first_block.pair_ids}
    pairs = {pair: service.get_lesson(pair) for pair in first_block.pair_ids}
    batches = service.redesign.list_manual_batches()
    cards = service.list_cards()
    target = service.courses.list_topics(semester.id)[1]
    corrected = service.courses.change_lesson_topic(first.id, target.id)
    assert corrected == replace(first, topic_id=target.id)
    assert service.redesign.get_block(first.block_id) == replace(first_block, title=target.title)
    assert service.redesign.get_block(other.block_id).title == "Тема 1"
    assert {pair: service.redesign.pair_evidence(pair) for pair in first_block.pair_ids} == evidence
    for pair, original in pairs.items():
        assert service.get_lesson(pair) == replace(original, title=target.title)
    assert service.redesign.list_manual_batches() == batches and service.list_cards() == cards
    assert service.grading.report(semester.id, students[0].student_id) == report
    assert service.courses.attendance(semester.id, students[0].student_id, target.id)
    assert not service.courses.attendance(semester.id, students[0].student_id, first.topic_id)


def test_programme_rename_keeps_topic_identity_and_updates_all_occurrences(course):
    service, semester, roster = course
    lessons = [
        service.courses.schedule(semester.id, roster.id, 1, date(2026, 9, day), 1) for day in (1, 8)
    ]
    before = service.courses.list_topics(semester.id)[0]
    renamed = service.courses.save_topic(semester.id, before.number, "Общее исправленное название")
    assert renamed.id == before.id
    assert all(
        service.redesign.get_block(lesson.block_id).title == renamed.title for lesson in lessons
    )
    assert service.courses.list_lessons(semester.id) == lessons


def test_retopic_cannot_cross_course_or_modify_deleted_occurrence(course):
    service, semester, roster = course
    lesson = service.courses.schedule(semester.id, roster.id, 1, date(2026, 9, 1), 1)
    copied = service.courses.copy_course(
        semester.id, semester.academic_year_id, 2, "Другой семестр"
    )
    foreign = service.courses.list_topics(copied.id)[0]
    with pytest.raises(AttendanceError):
        service.courses.change_lesson_topic(lesson.id, foreign.id)
    assert service.courses.get_lesson(lesson.id) == lesson
    block = service.redesign.get_block(lesson.block_id)
    service.lifecycle.delete_lesson(block.pair_ids[0], "Синтетическая проверка")
    with pytest.raises(AttendanceError):
        service.courses.change_lesson_topic(
            lesson.id, service.courses.list_topics(semester.id)[1].id
        )
    assert (
        next(item for item in service.repository.cw_lessons(semester.id) if item.id == lesson.id)
        == lesson
    )


def test_new_topic_and_canonical_lecture_stream_rollback_if_schedule_fails(course):
    service, original, _ = course
    lecture = service.courses.save_course(
        original.discipline_id, original.academic_year_id, 1, 2, "lecture", 1, "Планируемый курс"
    )
    with pytest.raises(AttendanceError):
        service.courses.schedule_with_topic(
            lecture.id, None, date(2026, 9, 1), 9, new_topic_number=1, new_topic_title="Новая тема"
        )
    assert service.courses.list_topics(lecture.id) == []
    assert service.courses.list_rosters(lecture.id) == []
    assert service.courses.list_lessons(lecture.id) == []


def test_new_series_topic_rolls_back_when_following_programme_topic_missing(course):
    service, semester, roster = course
    before = service.courses.list_topics(semester.id)
    with pytest.raises(AttendanceError):
        service.courses.schedule_with_topic(
            semester.id,
            roster.id,
            date(2026, 9, 1),
            1,
            new_topic_number=5,
            new_topic_title="Первая новая тема",
            interval_days=7,
            count=2,
        )
    assert service.courses.list_topics(semester.id) == before
    assert service.courses.list_lessons(semester.id) == []
    assert service.repository.cw_series(semester.id) == []
