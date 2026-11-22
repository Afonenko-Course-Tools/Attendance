import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime

import pytest
import test_course_workflow as course_tests

from attendance_lab.application.journal import (
    DEFAULT_REASON,
    JournalMarkRequest,
    JournalTopicTarget,
    confirm_journal_mark,
    prepare_journal_mark,
)
from attendance_lab.application.quick_journal import JournalActions
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import EXCUSED_ABSENCE_REASON
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def lesson(course):
    service, value, roster = course
    occurrence = service.courses.schedule(value.id, roster.id, 1, date(2026, 9, 1), 1)
    pairs = service.redesign.get_block(occurrence.block_id).pair_ids
    students = tuple(student.student_id for student in service.courses.list_students(value.id))
    return service, occurrence, pairs, students


@pytest.fixture
def course(tmp_path):
    yield from course_tests.course.__wrapped__(tmp_path)


def _prepare_toggle(service, occurrence, student_id):
    request = JournalMarkRequest(
        occurrence.course_id,
        frozenset({(student_id, occurrence.topic_id)}),
        toggle=True,
    )
    return prepare_journal_mark(service, request)


def test_toggle_revokes_one_students_two_pairs_in_a_shared_manual_batch(lesson):
    service, occurrence, pairs, (first, second) = lesson
    api = service.redesign
    batch = api.create_manual_batch(
        {(student, pair) for student in (first, second) for pair in pairs}, DEFAULT_REASON
    )

    pending = _prepare_toggle(service, occurrence, first)
    assert pending.mode == "revoke"
    result = confirm_journal_mark(service, pending, pending.automatic_targets)

    assert result.changed and result.mode == "revoke"
    [stored] = api.list_manual_batches()
    assert stored.targets == batch.targets
    assert set(stored.active_targets) == {(second, pair) for pair in pairs}
    assert stored.revoked_at is None
    assert {(student, pair) for student, pair, _ in stored.revoked_targets} == {
        (first, pair) for pair in pairs
    }
    assert service.courses.attendance_statuses(occurrence.course_id) == (
        {(second, occurrence.topic_id)},
        set(),
    )
    assert all(
        len(api.evidence(first, pair)) == 1 and not api.evidence(first, pair)[0].active
        for pair in pairs
    )
    [history] = JournalActions(service).execute(
        "journal.history",
        {
            "course_id": occurrence.course_id,
            "occurrence_id": occurrence.id,
            "cells": [{"student_id": first, "topic_id": occurrence.topic_id}],
        },
    )["history"]
    assert not history["active"] and datetime.fromisoformat(history["revoked_at"])
    assert history["targets"] == [[first, pair] for pair in pairs]


def test_toggle_keeps_card_evidence_and_leaves_card_only_presence_unchanged(lesson):
    service, occurrence, pairs, (first, second) = lesson
    api = service.redesign
    api.create_manual_batch({(first, pair) for pair in pairs}, DEFAULT_REASON)
    service.bind_card("001122AA", first)
    service.bind_card("001122BB", second)
    session = service.courses.open_registration(occurrence.id)
    for uid in ("001122AA", "001122BB"):
        api.record_session_scan(session.id, uid, datetime(2026, 9, 1, 6, 7, tzinfo=UTC))
    api.close_session(session.id)

    selection = _prepare_toggle(service, occurrence, first)
    result = confirm_journal_mark(service, selection, selection.automatic_targets)
    assert result.changed and result.mode == "revoke_card"
    assert _prepare_toggle(service, occurrence, first).mode == "card"
    assert _prepare_toggle(service, occurrence, second).mode == "card"
    assert api.toggle_attendance(second, pairs, allow_new=False) == "card"
    assert {item.uid for item in api.active_attendance(first, pairs)} == {"001122AA"}
    assert {item.uid for item in api.active_attendance(second, pairs)} == {"001122BB"}


def test_toggle_excused_absence_can_be_followed_by_a_new_manual_mark(lesson):
    service, occurrence, pairs, (first, _) = lesson
    api = service.redesign
    api.create_manual_batch({(first, pair) for pair in pairs}, EXCUSED_ABSENCE_REASON)
    assert api.toggle_attendance(first, pairs, allow_new=False) == "revoke"
    assert api.active_attendance(first, pairs) == []

    assert api.toggle_attendance(first, pairs, allow_new=True) == "mark"
    assert {item.kind for item in api.active_attendance(first, pairs)} == {"manual"}
    excused, manual = api.list_manual_batches()
    assert excused.active_targets == ()
    assert set(manual.active_targets) == {(first, pair) for pair in pairs}
    assert service.courses.attendance_statuses(occurrence.course_id) == (
        {(first, occurrence.topic_id)},
        set(),
    )


def test_clear_selected_pairs_revokes_all_manual_grounds_without_touching_other_cells(lesson):
    service, occurrence, (first_pair, second_pair), (first, second) = lesson
    api = service.redesign
    batch = api.create_manual_batch(
        {(student, pair) for student in (first, second) for pair in (first_pair, second_pair)},
        DEFAULT_REASON,
    )
    excuse = api.create_manual_batch({(first, first_pair)}, EXCUSED_ABSENCE_REASON)
    service.bind_card("001122AA", first)
    session = api.open_session(occurrence.block_id, {first_pair})
    api.record_session_scan(session.id, "001122AA", datetime(2026, 9, 1, 6, 7, tzinfo=UTC))
    api.close_session(session.id)

    selected = {(first, first_pair), (second, second_pair)}
    assert api.clear_attendance_cells(selected) == ({first, second}, "revoke_card")
    assert api.clear_attendance_cells(selected) == (frozenset(), "card")
    assert api.clear_attendance({second}, (second_pair,)) == (frozenset(), "empty")

    batches = {item.id: item for item in api.list_manual_batches()}
    assert set(batches[batch.id].active_targets) == {(first, second_pair), (second, first_pair)}
    assert not batches[excuse.id].active_targets
    assert {item.kind for item in api.active_attendance(first, [first_pair])} == {"card"}


def test_clear_journal_chooses_exact_occurrence_and_reports_actual_changed_cells(course, lesson):
    service, value, roster = course
    _, first, pairs, (student_a, student_b) = lesson
    api = service.redesign
    second = service.courses.schedule(value.id, roster.id, 1, date(2026, 9, 8), 1)
    other_pairs = api.get_block(second.block_id).pair_ids
    api.create_manual_batch(
        {(student_a, pair) for pair in pairs} | {(student_b, pair) for pair in other_pairs},
        DEFAULT_REASON,
    )
    cells = frozenset({(student_a, first.topic_id), (student_b, first.topic_id)})
    request = JournalMarkRequest(value.id, cells, revoke_only=True)
    pending = prepare_journal_mark(service, request)
    assert pending.requires_choice and pending.cells == cells
    with pytest.raises(AttendanceError, match="Явно выберите"):
        confirm_journal_mark(service, pending, ())

    result = confirm_journal_mark(service, pending, (JournalTopicTarget(first.topic_id, first.id),))
    assert result.marked_cells == {(student_a, first.topic_id)}
    assert result.mode == "revoke_partial"
    assert api.active_attendance(student_a, pairs) == []
    assert {item.pair_id for item in api.active_attendance(student_b, other_pairs)} == set(
        other_pairs
    )


def test_quick_clear_uses_the_visible_occurrence_and_never_adds_attendance(course, lesson):
    service, value, roster = course
    _, first, pairs, (student_a, student_b) = lesson
    api = service.redesign
    second = service.courses.schedule(value.id, roster.id, 1, date(2026, 9, 8), 1)
    other_pairs = api.get_block(second.block_id).pair_ids
    api.create_manual_batch({(student_a, pair) for pair in (*pairs, *other_pairs)}, DEFAULT_REASON)
    actions = JournalActions(service)
    payload = {
        "course_id": value.id,
        "revoke_only": True,
        "occurrence_id": first.id,
        "cells": [
            {"student_id": student_id, "topic_id": first.topic_id}
            for student_id in (student_a, student_b)
        ],
    }

    result = actions.execute("journal.prepare_mark", payload)
    assert result["changed"] and result["mode"] == "revoke"
    result = actions.execute("journal.prepare_mark", payload)
    assert not result["changed"] and result["mode"] == "empty"
    assert api.active_attendance(student_a, pairs) == api.active_attendance(student_b, pairs) == []
    assert {item.pair_id for item in api.active_attendance(student_a, other_pairs)} == set(
        other_pairs
    )
    assert len(api.list_manual_batches()) == 1


def test_stale_selection_preserves_a_replacement_mark_and_other_occurrences(course, lesson):
    service, value, roster = course
    _, first, pairs, (student, _) = lesson
    api = service.redesign
    second = service.courses.schedule(value.id, roster.id, 1, date(2026, 9, 8), 1)
    other_pairs = api.get_block(second.block_id).pair_ids
    batch = api.create_manual_batch(
        {(student, pair) for pair in (*pairs, *other_pairs)}, DEFAULT_REASON
    )
    pending = _prepare_toggle(service, first, student)
    assert pending.requires_choice
    selection = (JournalTopicTarget(first.topic_id, first.id),)

    deletion = service.lifecycle.delete_lesson(pairs[0], "Учебное удаление")
    with pytest.raises(AttendanceError, match="не найдено"):
        confirm_journal_mark(service, pending, selection)
    assert set(api.list_manual_batches()[0].active_targets) == set(batch.targets)
    service.lifecycle.restore_deletion(deletion.id)
    assert confirm_journal_mark(service, pending, selection).changed

    replacement = api.create_manual_batch({(student, pair) for pair in pairs}, DEFAULT_REASON)
    assert not confirm_journal_mark(service, pending, selection).changed
    original, new = api.list_manual_batches()
    assert new.id == replacement.id
    assert set(new.active_targets) == {(student, pair) for pair in pairs}
    assert set(original.active_targets) == {(student, pair) for pair in other_pairs}


def test_fully_revoked_legacy_session_mark_stays_revoked_after_expansion(lesson):
    service, occurrence, pairs, (first, second) = lesson
    api = service.redesign
    session = api.open_session(occurrence.block_id, {pairs[0]})
    revoked_batch = api.session_manual_mark(session.id, first, DEFAULT_REASON)
    kept_batch = api.session_manual_mark(session.id, second, DEFAULT_REASON)
    assert api.toggle_attendance(first, (pairs[0],), allow_new=False) == "revoke"

    api.expand_session(session.id)

    assert api.active_attendance(first, pairs) == []
    assert {item.pair_id for item in api.active_attendance(second, pairs)} == set(pairs)
    batches = {batch.id: batch for batch in api.list_manual_batches()}
    assert batches[revoked_batch.id].active_targets == ()
    assert set(batches[kept_batch.id].active_targets) == {(second, pair) for pair in pairs}
    assert api.session_manual_mark(session.id, first, DEFAULT_REASON).active_targets


def test_schema_v5_upgrade_keeps_legacy_batches_and_supports_target_revocation(lesson, tmp_path):
    service, _, pairs, (first, second) = lesson
    service.redesign.create_manual_batch(
        {(student, pair) for student in (first, second) for pair in pairs}, DEFAULT_REASON
    )
    database = tmp_path / "schema-v5.sqlite3"
    with (
        closing(sqlite3.connect(service.repository.path)) as source,
        closing(sqlite3.connect(database)) as copy,
    ):
        source.backup(copy)
        copy.execute("DROP TABLE course_score_variants")
        copy.execute("DROP TABLE manual_target_revocations")
        copy.execute("PRAGMA user_version=5")
        copy.commit()

    migrated = AttendanceService(SQLiteRepository(database, mode="demo"))
    try:
        api = migrated.redesign
        assert {item.pair_id for item in api.active_attendance(first, pairs)} == set(pairs)
        assert api.toggle_attendance(first, pairs, allow_new=False) == "revoke"
        assert api.active_attendance(first, pairs) == []
        assert {item.pair_id for item in api.active_attendance(second, pairs)} == set(pairs)
    finally:
        migrated.close()
