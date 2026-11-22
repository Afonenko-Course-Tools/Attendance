"""Real storage acceptance for the Qt Quick academic worker boundary."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest
import test_course_workflow as course_tests

from attendance_lab.application.course_commands import journal_mark_availability
from attendance_lab.application.operations import Operation, OperationExecutor
from attendance_lab.application.quick_academic import AcademicActions, json_safe
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.sqlite import SQLiteRepository

HEADERS = ["№", "ФИО", "Специальность", "Специализация/профилизация", "Курс", "Группа", "№ зачётки"]


@pytest.fixture
def academic(tmp_path):
    fixture = course_tests.course.__wrapped__(tmp_path)
    service, course, roster = next(fixture)
    yield AcademicActions(service, CsvFiles()), service, course, roster
    try:
        next(fixture)
    except StopIteration:
        pass


def test_snapshot_is_json_safe_and_journal_has_stable_ids_and_known_absences(academic):
    actions, service, course, roster = academic
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    before = actions.snapshot(course.id, lesson.id)
    json.dumps(before, ensure_ascii=False, allow_nan=False)
    assert before["course"]["id"] == course.id
    assert before["lessons"][0]["id"] == lesson.id
    assert before["journal"]["rows"][0]["cells"][0]["topic_id"] == lesson.topic_id
    assert before["journal"]["rows"][0]["missed_count"] == 1
    assert before["course_groups"] and before["course_students"]
    assert not before["journal"]["observed_only"]
    assert before["commands"]["view"]["enabled"]
    assert actions.snapshot(-1)["course"] is None
    assert json_safe(MappingProxyType({"ids": frozenset({1})})) == {"ids": [1]}


def test_course_create_edit_and_copy_preserve_programme_and_select_identity(academic):
    actions, service, course, _ = academic
    created = actions.execute(
        "academic.save_course",
        {
            "discipline_id": course.discipline_id,
            "academic_year_id": course.academic_year_id,
            "study_course": 2,
            "semester": 2,
            "kind": "seminar",
            "duration": 1,
            "name": "Новый курс",
        },
    )
    new_id = created["course_id"]
    assert created["select_course_id"] == new_id
    actions.execute("academic.add_topics", {"course_id": new_id, "first_number": 1, "count": 3})
    original_ids = [topic.id for topic in service.courses.list_topics(new_id)]
    actions.execute(
        "academic.topic", {"course_id": new_id, "topic_id": original_ids[0], "title": "Уточнённая"}
    )
    copied = actions.execute(
        "academic.copy_course",
        {
            "course_id": new_id,
            "academic_year_id": course.academic_year_id,
            "semester": 1,
            "name": "Копия",
        },
    )
    assert copied["course_id"] != new_id
    assert [topic.title for topic in service.courses.list_topics(copied["course_id"])] == [
        "Уточнённая",
        "Тема 2",
        "Тема 3",
    ]
    with pytest.raises(AttendanceError):
        actions.execute(
            "academic.topic",
            {"course_id": course.id, "topic_id": original_ids[0], "title": "Чужая тема"},
        )


def test_schedule_series_move_recurrence_and_failed_series_are_atomic(academic):
    actions, service, course, roster = academic
    payload = {
        "course_id": course.id,
        "roster_id": roster.id,
        "date": "2026-09-01",
        "start_slot": 1,
        "topic_id": service.courses.list_topics(course.id)[0].id,
        "interval_days": 7,
        "count": 3,
    }
    created = actions.execute("academic.schedule", payload)
    assert len(created["lesson_ids"]) == 3
    first = created["lesson_ids"][0]
    actions.execute(
        "academic.move",
        {"course_id": course.id, "lesson_id": first, "date": "2026-09-02", "start_slot": 3},
    )
    actions.execute(
        "academic.recurrence",
        {"course_id": course.id, "lesson_id": first, "date": "2026-09-02", "interval_days": 14},
    )
    lessons = service.courses.list_lessons(course.id)
    assert service.redesign.get_block(lessons[0].block_id).date == date(2026, 9, 2)
    count = len(lessons)
    with pytest.raises(AttendanceError):
        actions.execute(
            "academic.schedule",
            dict(payload, topic_id=service.courses.list_topics(course.id)[-1].id, count=3),
        )
    assert len(service.courses.list_lessons(course.id)) == count
    assert len(service.courses.store.cw_series(course.id)) == 1


def test_delete_restore_and_cross_course_guards_keep_original_evidence(academic):
    actions, service, course, roster = academic
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    student = service.courses.list_students(course.id)[0].student_id
    actions.execute(
        "journal.prepare_mark",
        {"course_id": course.id, "cells": [{"student_id": student, "topic_id": lesson.topic_id}]},
    )
    other = service.courses.copy_course(course.id, course.academic_year_id, 2, "Другой")
    with pytest.raises(AttendanceError):
        actions.execute(
            "academic.delete", {"course_id": other.id, "lesson_id": lesson.id, "reason": "Ошибка"}
        )
    removed = actions.execute(
        "academic.delete", {"course_id": course.id, "lesson_id": lesson.id, "reason": "Тестовое"}
    )
    assert actions.snapshot(course.id)["lessons"] == []
    actions.execute("academic.restore", removed)
    assert actions.snapshot(course.id)["lessons"][0]["id"] == lesson.id
    assert (student, lesson.topic_id) in service.courses.attendance_projection(course.id)


def test_journal_chooser_rechecks_scan_and_revoke_preserves_card_evidence(academic):
    actions, service, course, roster = academic
    first = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    second = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 8), 1)
    students = service.courses.list_students(course.id)
    cells = [{"student_id": student.student_id, "topic_id": first.topic_id} for student in students]
    prepared = actions.execute("journal.prepare_mark", {"course_id": course.id, "cells": cells})
    assert prepared["requires_choice"]
    pending = actions.snapshot(course.id)["mark_preparation"]
    assert pending["token"] == prepared["token"]
    service.bind_card("0001AB", students[0].student_id)
    session = service.courses.open_registration(first.id)
    service.redesign.record_session_scan(
        session.id, "0001AB", datetime(2026, 9, 1, 6, 1, tzinfo=UTC)
    )
    service.redesign.close_session(session.id)
    confirmed = actions.execute(
        "journal.confirm_mark",
        {
            "course_id": course.id,
            "token": prepared["token"],
            "targets": [{"topic_id": first.topic_id, "occurrence_id": second.id}],
            "reason": "отработал с другой группой",
        },
    )
    assert confirmed["changed"]
    history = actions.execute("journal.history", {"course_id": course.id, "cells": cells})[
        "history"
    ]
    assert len(history) == 1 and history[0]["students"] == [students[1].full_name]
    actions.execute("journal.revoke", {"course_id": course.id, "batch_id": history[0]["id"]})
    present = service.courses.attendance_projection(course.id)
    assert (students[0].student_id, first.topic_id) in present
    assert (students[1].student_id, first.topic_id) not in present


def test_journal_stale_token_and_retargeted_offered_identity_do_not_write(academic):
    actions, service, course, roster = academic
    first = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 8), 1)
    student = service.courses.list_students(course.id)[0].student_id
    prepared = actions.execute(
        "journal.prepare_mark",
        {"course_id": course.id, "cells": [{"student_id": student, "topic_id": first.topic_id}]},
    )
    service.courses.change_lesson_topic(first.id, service.courses.list_topics(course.id)[1].id)
    payload = {
        "course_id": course.id,
        "token": prepared["token"],
        "targets": [{"topic_id": first.topic_id, "occurrence_id": first.id}],
    }
    with pytest.raises(AttendanceError):
        actions.execute("journal.confirm_mark", payload)
    actions.execute("journal.cancel_mark", prepared)
    with pytest.raises(AttendanceError, match="устарел"):
        actions.execute("journal.confirm_mark", payload)
    assert service.redesign.list_manual_batches() == []


def test_journal_search_sort_and_missing_filter_preserve_identity(academic):
    actions, service, course, roster = academic
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    students = service.courses.list_students(course.id)
    actions.execute(
        "journal.prepare_mark",
        {
            "course_id": course.id,
            "cells": [{"student_id": students[0].student_id, "topic_id": lesson.topic_id}],
        },
    )
    actions.execute(
        "journal.filter",
        {
            "course_id": course.id,
            "status": "missing",
            "threshold": 1,
            "sort": "missed_count",
            "descending": True,
        },
    )
    rows = actions.snapshot(course.id)["journal"]["rows"]
    assert [row["id"] for row in rows] == [students[1].student_id]
    actions.execute(
        "journal.filter", {"course_id": course.id, "status": "all", "query": students[0].full_name}
    )
    assert [row["id"] for row in actions.snapshot(course.id)["journal"]["rows"]] == [
        students[0].student_id
    ]


def test_import_review_revision_and_file_content_are_rechecked(academic, tmp_path):
    actions, service, _, _ = academic
    path = tmp_path / "import.csv"
    actions.students.files.write_csv(
        path, HEADERS, [["1", "Учебный Новый", "Тест", "Профиль", "2", "1", "0003"]]
    )
    reviewed = actions.execute(
        "students.preview_import", {"path": path.as_uri(), "academic_year_start": 2026}
    )
    assert reviewed["can_apply"] and len(service.list_students()) == 2
    actions.students.files.write_csv(
        path, HEADERS, [["1", "Учебный Подмена", "Тест", "Профиль", "2", "1", "0003"]]
    )
    with pytest.raises(AttendanceError, match="CSV изменился"):
        actions.execute("students.apply_import", reviewed)
    assert len(service.list_students()) == 2
    reviewed = actions.execute(
        "students.preview_import", {"path": str(path), "academic_year_start": 2026}
    )
    service.redesign.save_year(2027)
    with pytest.raises(AttendanceError, match="Справочник изменился"):
        actions.execute("students.apply_import", reviewed)
    reviewed = actions.execute(
        "students.preview_import", {"path": str(path), "academic_year_start": 2026}
    )
    assert actions.execute("students.apply_import", reviewed)["imported"] == 1
    assert len(service.list_students()) == 3
    assert any(
        student["record_book_number"] == "0003" for student in actions.snapshot()["students"]
    )


def test_student_group_correction_keeps_id_and_reviewed_unbind_guards_owner(academic):
    actions, service, _, _ = academic
    student = service.redesign.list_student_contexts()[0]
    group = service.redesign.list_groups()[0]
    other_group = service.redesign.save_group(group.academic_year_id, group.specialty_id, 2, 2)
    saved = actions.execute(
        "students.save_context",
        {
            "student_id": student.student_id,
            "full_name": "Уточнённый Студент",
            "record_book_number": student.record_book_number,
            "academic_year_id": group.academic_year_id,
            "group_id": other_group.id,
        },
    )
    assert saved["student_id"] == student.student_id
    service.bind_card("000ABC", student.student_id)
    with pytest.raises(AttendanceError, match="Набор карт"):
        actions.execute("students.unbind", {"student_id": student.student_id, "expected_cards": []})
    actions.execute(
        "students.unbind",
        {
            "student_id": student.student_id,
            "expected_cards": next(
                row["cards"]
                for row in actions.snapshot()["students"]
                if row["id"] == student.student_id
            ),
        },
    )
    history = actions.execute("students.history", {"student_id": student.student_id})
    assert [row["uid"] for row in history["bindings"]] == ["000ABC", "000ABC"]
    assert history["bindings"][0]["student_id"] is None


def test_journal_table_rectangle_and_neighbor_use_current_ids(academic):
    from PySide6.QtCore import Qt

    from attendance_lab.presentation.quick.academic_models import JournalTableModel

    actions, service, course, roster = academic
    service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    journal = actions.snapshot(course.id)["journal"]
    model = JournalTableModel()
    model.set_snapshot(journal)
    resets, updates = [], []
    model.modelReset.connect(lambda: resets.append(True))
    model.dataChanged.connect(lambda *_: updates.append(True))
    first, second = journal["rows"]
    topics = journal["topics"]
    assert model.columnCount() == 6 + len(topics)
    assert [model.headerData(i, Qt.Orientation.Horizontal) for i in range(5)] == [
        "ФИО",
        "Группа",
        "Подгруппа",
        "Специальность",
        "Профиль",
    ]
    assert model.headerData(5, Qt.Orientation.Horizontal) == str(topics[0]["number"])
    assert model.headerData(model.columnCount() - 1, Qt.Orientation.Horizontal) == "Пропуски"
    assert model.data(model.index(0, 5), model.TOPIC) == topics[0]["id"]
    assert model.data(model.index(0, 5)) == "—"
    assert model.data(model.index(0, model.columnCount() - 1)) == first["absence_text"]
    assert model.data(model.index(0, model.columnCount() - 1), model.TOPIC) == -1
    rectangle = model.rectangle(first["id"], topics[0]["id"], second["id"], topics[1]["id"])
    assert len(rectangle) == 4
    assert model.neighbor(first["id"], topics[0]["id"], 1, 1) == {
        "student_id": second["id"],
        "topic_id": topics[1]["id"],
    }
    model.set_snapshot(dict(journal, rows=[second, first]))
    assert resets == [True]
    assert model.rectangle("stale", topics[0]["id"], first["id"], topics[1]["id"]) == []
    assert model.data(model.index(0, 0)) == second["name"]
    model.set_snapshot(dict(journal, rows=[dict(second, absence_text="2"), first]))
    assert resets == [True] and updates == [True]

    # View sorting must also change the stable-ID keyboard/rectangle topology.
    model.set_view_sort("name", True)
    assert model.rows[0]["name"] == max(first["name"], second["name"])
    assert (
        model.neighbor(model.rows[0]["id"], topics[0]["id"], 1, 0)["student_id"]
        == model.rows[1]["id"]
    )
    assert len(model.rectangle(first["id"], topics[0]["id"], second["id"], topics[1]["id"])) == 4
    resets_before = len(resets)
    model.set_view_sort("name", True)
    assert len(resets) == resets_before


def test_reviewed_student_cards_reject_owner_aba_even_when_uid_set_matches(academic):
    actions, service, _, _ = academic
    first, second = service.list_students()
    service.bind_card("00AABB", first.id)
    reviewed = next(row["cards"] for row in actions.snapshot()["students"] if row["id"] == first.id)
    service.bind_card("00AABB", second.id, correction=True, expected_student_id=first.id)
    service.bind_card("00AABB", first.id, correction=True, expected_student_id=second.id)
    with pytest.raises(AttendanceError, match="История привязки"):
        actions.execute("students.unbind", {"student_id": first.id, "expected_cards": reviewed})
    assert service.repository.get_card("00AABB").student_id == first.id
    with pytest.raises(AttendanceError, match="Привязка карты изменилась"):
        actions.execute(
            "students.bind", {"uid": "00AABB", "student_id": second.id, "correction": True}
        )


def test_observed_only_journal_does_not_enroll_or_invent_absence_counts(academic):
    actions, service, original, original_roster = academic
    course = service.courses.save_course(
        original.discipline_id, original.academic_year_id, 2, 2, "lab", 2, "Без состава"
    )
    with service.repository.transaction():
        roster = service.repository.rd_save_roster(
            replace(
                original_roster,
                id=0,
                semester=2,
                name="Историческая линия",
                mode="students",
                group_ids=(),
                student_ids=(),
            )
        )
        service.courses.add_roster(course.id, roster.id)
    lesson = service.courses.schedule_with_topic(
        course.id, roster.id, date(2026, 9, 1), 1, new_topic_number=1, new_topic_title="Факт"
    )
    student = service.list_students()[0]
    actions.execute(
        "journal.prepare_mark",
        {
            "course_id": course.id,
            "cells": [{"student_id": student.id, "topic_id": lesson.topic_id}],
        },
    )
    journal = actions.snapshot(course.id)["journal"]
    assert journal["observed_only"] and journal["rows"][0]["id"] == student.id
    assert journal["rows"][0]["absence_text"] == "—"
    assert service.courses.list_students(course.id) == []
    actions.execute("journal.filter", {"course_id": course.id, "status": "missing"})
    assert actions.snapshot(course.id)["journal"]["rows"] == []


def test_roster_edit_cannot_cross_study_course_and_topic_edit_keeps_lesson_id(academic):
    actions, service, course, roster = academic
    group = service.redesign.list_groups()[0]
    other_group = service.redesign.save_group(group.academic_year_id, group.specialty_id, 3, 1)
    service.redesign.set_discipline_groups(
        course.discipline_id, course.academic_year_id, {group.id, other_group.id}
    )
    with pytest.raises(AttendanceError, match="курсу обучения"):
        actions.execute(
            "academic.save_roster",
            {"course_id": course.id, "roster_id": roster.id, "group_ids": [other_group.id]},
        )
    assert service.courses.list_rosters(course.id)[0].group_ids == roster.group_ids
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    target = service.courses.list_topics(course.id)[1]
    result = actions.execute(
        "academic.change_topic",
        {"course_id": course.id, "lesson_id": lesson.id, "topic_id": target.id},
    )
    assert result["lesson_id"] == lesson.id
    assert service.courses.get_lesson(lesson.id).topic_id == target.id


def test_failed_new_course_rolls_back_inline_discipline(academic):
    actions, service, _, _ = academic
    before = service.list_disciplines()
    with pytest.raises(AttendanceError):
        actions.execute(
            "academic.save_course",
            {
                "name": "Не сохранится",
                "discipline_name": "Не сохранять отдельно",
                "academic_year_id": 9999,
            },
        )
    assert service.list_disciplines() == before


def test_import_preview_echoes_view_revision_and_manual_history_is_course_scoped(academic):
    actions, service, course, roster = academic
    preview = actions.execute(
        "students.preview_import",
        {
            "headers": HEADERS,
            "rows": [["1", "Новый Тестовый", "Тест", "Профиль", "2", "1", "0003"]],
            "academic_year_start": 2026,
            "view_revision": 7,
        },
    )
    assert preview["view_revision"] == 7
    assert actions.snapshot()["import_preview"]["view_revision"] == 7
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    student = service.list_students()[0]
    actions.execute(
        "journal.prepare_mark",
        {
            "course_id": course.id,
            "cells": [{"student_id": student.id, "topic_id": lesson.topic_id}],
        },
    )
    actions.execute("journal.history", {"course_id": course.id})
    other = service.courses.copy_course(course.id, course.academic_year_id, 2, "Другой")
    assert actions.snapshot(course.id)["manual_history"]
    assert not actions.snapshot(other.id)["manual_history"]


def test_durable_ambiguous_prepare_reconstructs_choices_after_storage_restart(academic):
    actions, service, course, roster = academic
    first = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    second = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 8), 1)
    student = service.list_students()[0]
    operation = Operation.create(
        "review-before-restart",
        "journal.prepare_mark",
        {
            "course_id": course.id,
            "cells": [{"student_id": student.id, "topic_id": first.topic_id}],
        },
    )
    saved = OperationExecutor(service.repository).execute(
        operation, lambda: actions.execute(operation.kind, json.loads(operation.payload_json))
    )
    path = service.repository.path
    service.close()
    reopened = AttendanceService(SQLiteRepository(path, mode="demo"))
    try:
        fresh = AcademicActions(reopened, CsvFiles())
        replay = OperationExecutor(reopened.repository).execute(
            operation,
            lambda: pytest.fail("Committed preparation must be replayed from its receipt"),
        )
        assert replay == saved
        fresh.restore_result(operation.kind, replay)
        assert fresh.snapshot(course.id)["mark_preparation"]["token"] == replay["token"]
        confirmation = Operation.create(
            "confirm-after-restart",
            "journal.confirm_mark",
            {
                "course_id": course.id,
                "token": replay["token"],
                "targets": [{"topic_id": first.topic_id, "occurrence_id": second.id}],
                "reason": "отработал с другой группой",
            },
        )
        executor = OperationExecutor(reopened.repository)
        result = executor.execute(
            confirmation,
            lambda: fresh.execute(confirmation.kind, json.loads(confirmation.payload_json)),
        )
        assert result["changed"]
        assert (
            executor.execute(
                confirmation, lambda: pytest.fail("Confirmation replay must not write again")
            )
            == result
        )
        assert len(reopened.redesign.list_manual_batches()) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "cells,scheduled,present,enabled",
    [
        ([], [1], [], False),
        ([("s", 1)], [1], [], True),
        ([("s", 1)], [1], [("s", 1)], False),
        ([("s", 1), ("s", 2)], [1], [("s", 1)], False),
        ([("s", 1), ("s", 2)], [1, 2], [("s", 1)], True),
    ],
)
def test_journal_activation_uses_one_availability_rule(cells, scheduled, present, enabled):
    assert journal_mark_availability(cells, scheduled, present).enabled is enabled
