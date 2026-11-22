"""UI-free manual journal contracts and stale-selection regressions."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest
import test_course_workflow as course_tests

from attendance_lab.application.course_absences import journal_absences
from attendance_lab.application.journal import (
    DEFAULT_REASON,
    MANUAL_REASONS,
    JournalMarkRequest,
    JournalTopicTarget,
    confirm_journal_mark,
    prepare_journal_mark,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import EXCUSED_ABSENCE_REASON


@pytest.fixture
def course(tmp_path):
    yield from course_tests.course.__wrapped__(tmp_path)


def prepared(course, *, repeated=False):
    service, value, roster = course
    first = service.courses.schedule(value.id, roster.id, 1, date(2026, 9, 1), 1)
    second = (
        service.courses.schedule(value.id, roster.id, 1, date(2026, 9, 8), 3) if repeated else None
    )
    students = service.courses.list_students(value.id)
    request = JournalMarkRequest(
        value.id, frozenset((student.student_id, first.topic_id) for student in students)
    )
    return service, first, second, students, prepare_journal_mark(service, request)


def test_unique_instance_has_immutable_choices_and_marks_whole_block(course):
    service, occurrence, _, students, preparation = prepared(course)
    assert not preparation.requires_choice
    assert preparation.automatic_targets == (
        JournalTopicTarget(occurrence.topic_id, occurrence.id),
    )
    choice = preparation.topics[0].occurrences[0]
    assert choice.starts_at == datetime(2026, 9, 1, 9, tzinfo=choice.starts_at.tzinfo)
    assert choice.ends_at.hour == 11 and choice.ends_at.minute == 50
    assert preparation.topics[0].student_names == tuple(sorted(s.full_name for s in students))
    with pytest.raises(FrozenInstanceError):
        preparation.course_id = 99
    result = confirm_journal_mark(service, preparation, preparation.automatic_targets)
    assert result.changed and result.marked_cells == preparation.cells
    block = service.redesign.get_block(occurrence.block_id)
    for pair_id in block.pair_ids:
        assert {e.student_id for e in service.redesign.pair_evidence(pair_id)} == {
            student.student_id for student in students
        }
    assert service.redesign.list_manual_batches()[0].reason == DEFAULT_REASON
    assert not confirm_journal_mark(service, preparation, preparation.automatic_targets).changed
    assert len(service.redesign.list_manual_batches()) == 1


def test_excused_absence_is_revocable_and_yields_to_attendance(course):
    service, occurrence, _, students, preparation = prepared(course)
    course_id = preparation.course_id
    student = students[0]
    cell = (student.student_id, occurrence.topic_id)
    excuse = prepare_journal_mark(
        service, JournalMarkRequest(course_id, frozenset({cell}), EXCUSED_ABSENCE_REASON)
    )
    targets = excuse.automatic_targets
    assert (
        occurrence.topic_id
        in journal_absences(service, course_id).missed_topics[student.student_id]
    )
    assert confirm_journal_mark(service, excuse, targets).marked_cells == {cell}
    assert not confirm_journal_mark(service, excuse, targets).changed
    assert service.courses.attendance_statuses(course_id) == (set(), {cell})
    assert (
        occurrence.topic_id
        not in journal_absences(service, course_id).missed_topics[student.student_id]
    )
    for pair_id in service.redesign.get_block(occurrence.block_id).pair_ids:
        assert service.lesson_counts(pair_id).identified_students == 0
    _, rows = service.grading.export_projection(course_id)
    assert EXCUSED_ABSENCE_REASON in next(row[6] for row in rows if row[0] == student.full_name)

    confirmed = prepare_journal_mark(service, JournalMarkRequest(course_id, frozenset({cell})))
    assert confirm_journal_mark(service, confirmed, targets).changed
    assert service.courses.attendance_statuses(course_id) == ({cell}, set())
    excused_batch, presence_batch = service.redesign.list_manual_batches()
    service.redesign.revoke_manual_batch(presence_batch.id)
    assert service.courses.attendance_statuses(course_id) == (set(), {cell})
    service.redesign.revoke_manual_batch(excused_batch.id)
    assert (
        occurrence.topic_id
        in journal_absences(service, course_id).missed_topics[student.student_id]
    )


def test_ambiguous_instance_requires_explicit_offered_identity_and_reason(course):
    service, first, second, _, preparation = prepared(course, repeated=True)
    assert preparation.requires_choice and preparation.automatic_targets == ()
    for targets in ((), (JournalTopicTarget(first.topic_id, 999999),)):
        with pytest.raises(AttendanceError):
            confirm_journal_mark(service, preparation, targets)
    assert service.redesign.list_manual_batches() == []
    assert second is not None
    result = confirm_journal_mark(
        service,
        preparation,
        (JournalTopicTarget(first.topic_id, second.id),),
        reason=MANUAL_REASONS[1],
    )
    assert result.changed
    first_block = service.redesign.get_block(first.block_id)
    assert all(not service.redesign.pair_evidence(pair) for pair in first_block.pair_ids)
    assert service.redesign.list_manual_batches()[0].reason == MANUAL_REASONS[1]


def test_recheck_excludes_only_cells_confirmed_while_choosing(course):
    service, first, second, students, preparation = prepared(course, repeated=True)
    assert second is not None
    service.bind_card("0011AB", students[0].student_id)
    session = service.courses.open_registration(first.id)
    service.redesign.record_session_scan(
        session.id, "0011AB", datetime(2026, 9, 1, 6, 1, tzinfo=UTC)
    )
    service.redesign.close_session(session.id)
    result = confirm_journal_mark(
        service, preparation, (JournalTopicTarget(first.topic_id, second.id),)
    )
    assert result.marked_cells == {(students[1].student_id, first.topic_id)}
    block = service.redesign.get_block(second.block_id)
    assert all(
        {e.student_id for e in service.redesign.pair_evidence(pair)} == {students[1].student_id}
        for pair in block.pair_ids
    )


def test_deleted_choice_is_not_replaced_by_other_available_occurrence(course):
    service, first, _, _, preparation = prepared(course, repeated=True)
    block = service.redesign.get_block(first.block_id)
    service.lifecycle.delete_lesson(block.pair_ids[0], "Учебное удаление")
    with pytest.raises(AttendanceError):
        confirm_journal_mark(service, preparation, (JournalTopicTarget(first.topic_id, first.id),))
    assert service.redesign.list_manual_batches() == []


def test_retargeted_choice_cannot_mark_either_old_or_new_topic(course):
    service, first, _, _, preparation = prepared(course, repeated=True)
    other_topic = service.courses.list_topics(preparation.course_id)[1]
    service.courses.change_lesson_topic(first.id, other_topic.id)
    with pytest.raises(AttendanceError, match="не соответствует теме и курсу"):
        confirm_journal_mark(service, preparation, (JournalTopicTarget(first.topic_id, first.id),))
    assert service.redesign.list_manual_batches() == []
    assert service.courses.attendance_projection(preparation.course_id) == set()


def test_new_live_occurrence_must_be_offered_again_before_confirmation(course):
    service, first, _, _, preparation = prepared(course)
    _, _, roster = course
    later = service.courses.schedule(preparation.course_id, roster.id, 1, date(2026, 9, 8), 3)
    target = (JournalTopicTarget(first.topic_id, later.id),)
    with pytest.raises(AttendanceError, match="не соответствует теме и курсу"):
        confirm_journal_mark(service, preparation, target)
    assert service.redesign.list_manual_batches() == []
    refreshed = prepare_journal_mark(
        service, JournalMarkRequest(preparation.course_id, preparation.cells)
    )
    assert refreshed.requires_choice
    assert confirm_journal_mark(service, refreshed, target).changed


def test_duplicate_or_extra_topic_targets_are_rejected_without_writing(course):
    service, first, _, _, preparation = prepared(course)
    target = preparation.automatic_targets[0]
    other_topic = service.courses.list_topics(preparation.course_id)[1]
    for targets in ((target, target), (target, JournalTopicTarget(other_topic.id, first.id))):
        with pytest.raises(AttendanceError, match="Явно выберите занятие"):
            confirm_journal_mark(service, preparation, targets)
    assert service.redesign.list_manual_batches() == []
    assert service.courses.attendance_projection(preparation.course_id) == set()


def test_failed_confirmation_rolls_back_entire_manual_batch(course, monkeypatch):
    service, _, _, _, preparation = prepared(course)
    mark = service.courses.mark_topics

    def fail_after_write(*args):
        mark(*args)
        raise AttendanceError("Ошибка после записи")

    monkeypatch.setattr(service.courses, "mark_topics", fail_after_write)
    with pytest.raises(AttendanceError, match="Ошибка после записи"):
        confirm_journal_mark(service, preparation, preparation.automatic_targets)
    assert service.redesign.list_manual_batches() == []
    assert service.courses.attendance_projection(preparation.course_id) == set()


def test_prepare_rejects_unknown_student_foreign_topic_and_missing_schedule(course):
    service, value, _ = course
    student = service.courses.list_students(value.id)[0].student_id
    topic = service.courses.list_topics(value.id)[0]
    other = service.courses.copy_course(value.id, value.academic_year_id, 2, "Другой семестр")
    foreign = service.courses.list_topics(other.id)[0]
    for cells, message in (
        (frozenset({("missing", topic.id)}), "студент больше недоступен"),
        (frozenset({(student, foreign.id)}), "не относится к этому курсу"),
        (frozenset({(student, topic.id)}), "ещё нет занятия в расписании"),
    ):
        with pytest.raises(AttendanceError, match=message):
            prepare_journal_mark(service, JournalMarkRequest(value.id, cells))
    assert service.redesign.list_manual_batches() == []


def test_invalid_reason_is_rejected_at_preparation_and_confirmation(course):
    service, _, _, _, preparation = prepared(course)
    with pytest.raises(AttendanceError, match="причину"):
        prepare_journal_mark(
            service, JournalMarkRequest(preparation.course_id, preparation.cells, "")
        )
    with pytest.raises(AttendanceError, match="причину"):
        confirm_journal_mark(service, preparation, preparation.automatic_targets, reason="")
    assert service.redesign.list_manual_batches() == []
