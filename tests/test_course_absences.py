"""Pure application regressions for synthetic migrated and current membership."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from attendance_lab.application.course_absences import course_absences, expected_student_ids
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import TeachingRoster
from attendance_lab.infrastructure.sqlite import SQLiteRepository

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
PAST = date(2026, 9, 1)


@pytest.fixture
def lab(tmp_path):
    service = AttendanceService(
        SQLiteRepository(tmp_path / "synthetic-absence.sqlite3", mode="demo")
    )
    headers = [
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    ]
    rows = [
        ["1", "Учебный Один", "Тест", "Профиль", "2", "1", "0001"],
        ["2", "Учебный Два", "Тест", "Профиль", "2", "1", "0002"],
        ["3", "Учебный Три", "Тест", "Профиль", "2", "2", "0003"],
    ]
    service.redesign.apply_import(service.redesign.preview_import(headers, rows, 2026))
    groups = service.redesign.list_groups()
    discipline = service.save_discipline("Учебная дисциплина")
    course = service.courses.save_course(
        discipline.id, groups[0].academic_year_id, 1, 2, "lecture", 1, "Учебные лекции"
    )
    roster = service.courses.add_groups(course.id, {groups[0].id})
    for number in range(1, 5):
        service.courses.save_topic(course.id, number, f"Учебная тема {number}")
    students = sorted(service.courses.list_students(course.id), key=lambda s: s.record_book_number)
    yield service, course, roster, students, groups
    service.close()


@pytest.fixture
def seminar(lab):
    service, base, _, students, groups = lab
    course = service.courses.save_course(
        base.discipline_id, base.academic_year_id, 1, 2, "seminar", 1, "Учебные семинары"
    )
    roster = service.courses.add_groups(course.id, {groups[0].id})
    for number in range(1, 5):
        service.courses.save_topic(course.id, number, f"Тема семинара {number}")
    students = sorted(service.courses.list_students(course.id), key=lambda s: s.record_book_number)
    return service, course, roster, students, groups


def unassign_occurrence(service, course, occurrence):
    with service.repository.transaction():
        roster = service.repository.rd_save_roster(
            TeachingRoster(
                0,
                course.discipline_id,
                course.academic_year_id,
                course.semester,
                "Линия без состава",
                course.kind,
                course.duration,
                "students",
                (),
                (),
            )
        )
        service.courses.add_roster(course.id, roster.id)
        block = service.redesign.get_block(occurrence.block_id)
        service.repository.rd_save_block(replace(block, roster_id=roster.id))


def freeze_legacy(service, occurrence, student_ids):
    block = service.redesign.get_block(occurrence.block_id)
    with service.repository.transaction():
        for pair_id in block.pair_ids:
            pair = service.get_lesson(pair_id)
            service.repository.put_lesson(replace(pair, roster_fixed=True, state="completed"))
            service.repository.put_lesson_roster(pair_id, set(student_ids))
    return block


def test_legacy_expected_ids_count_missing_without_rich_context(seminar):
    service, course, roster, students, _ = seminar
    occurrence = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    block = freeze_legacy(service, occurrence, [s.student_id for s in students])
    assert service.repository.rd_context_snapshot(block.pair_ids[0]) == []
    result = course_absences(service, course.id, now=NOW)
    assert not result.incomplete
    assert all(result.missed_topics[s.student_id] == {occurrence.topic_id} for s in students)


def test_unassigned_frozen_seminar_is_unknown_despite_other_rosters_and_scan(seminar):
    service, course, roster, students, _ = seminar
    occurrence = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    block = freeze_legacy(service, occurrence, [])
    unassign_occurrence(service, course, occurrence)
    service.redesign.create_manual_batch(
        {(students[0].student_id, block.pair_ids[0])}, "Учебное посещение"
    )
    result = course_absences(service, course.id, now=NOW)
    assert result.incomplete
    assert result.unresolved_lesson_ids == (occurrence.id,)
    assert result.unresolved_topic_ids == {occurrence.topic_id}
    assert all(not topics for topics in result.expected_topics.values())
    assert all(not topics for topics in result.missed_topics.values())


def test_lecture_history_requires_whole_current_course_stream(lab):
    service, course, roster, students, groups = lab
    occurrence = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    freeze_legacy(service, occurrence, [s.student_id for s in students])
    service.courses.add_groups(course.id, {groups[1].id})
    newcomer = next(
        s for s in service.courses.list_students(course.id) if s.group_id == groups[1].id
    )
    result = course_absences(service, course.id, now=NOW)
    assert result.missed_topics[newcomer.student_id] == {occurrence.topic_id}
    assert result.missed_topics[students[0].student_id] == {occurrence.topic_id}


def test_cross_roster_same_topic_closes_gap_but_other_course_does_not(lab):
    service, course, roster, students, _ = lab
    first = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    first_block = freeze_legacy(service, first, [s.student_id for s in students])
    second = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 8), 1)
    second_block = freeze_legacy(service, second, [s.student_id for s in students])
    service.redesign.create_manual_batch(
        {(students[0].student_id, second_block.pair_ids[0])}, "Та же тема"
    )
    other = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 2, 2, "lecture", 1, "Другой семестр"
    )
    other_roster = service.courses.add_groups(other.id, {students[0].group_id})
    service.courses.save_topic(other.id, 1, "Другая тема")
    other_lesson = service.courses.schedule(other.id, other_roster.id, 1, PAST, 3)
    other_block = freeze_legacy(service, other_lesson, [s.student_id for s in students])
    service.redesign.create_manual_batch(
        {(students[1].student_id, other_block.pair_ids[0])}, "Другой курс"
    )
    result = course_absences(service, course.id, now=NOW)
    assert not result.missed_topics[students[0].student_id]
    assert result.missed_topics[students[1].student_id] == {first.topic_id}
    service.lifecycle.delete_lesson(first_block.pair_ids[0], "Учебное удаление")
    service.lifecycle.delete_lesson(second_block.pair_ids[0], "Учебное удаление")
    assert all(not v for v in course_absences(service, course.id, now=NOW).missed_topics.values())


def test_unknown_and_known_misses_remain_separate_and_future_is_ignored(seminar):
    service, course, roster, students, _ = seminar
    known = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    freeze_legacy(service, known, [s.student_id for s in students])
    unknown = service.courses.schedule(course.id, roster.id, 2, date(2026, 9, 8), 1)
    freeze_legacy(service, unknown, [])
    unassign_occurrence(service, course, unknown)
    future = service.courses.schedule(course.id, roster.id, 3, date(2027, 1, 1), 1)
    freeze_legacy(service, future, [])
    result = course_absences(service, course.id, now=NOW)
    assert result.incomplete and result.unresolved_lesson_ids == (unknown.id,)
    assert all(v == {known.topic_id} for v in result.missed_topics.values())
    with pytest.raises(AttendanceError):
        course_absences(service, course.id, now=datetime(2026, 11, 10))


def test_unfixed_lecture_uses_expected_stream_and_lab_uses_only_subgroup(lab):
    service, course, roster, students, _ = lab
    lecture = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    assert all(
        v == {lecture.topic_id}
        for v in course_absences(service, course.id, now=NOW).missed_topics.values()
    )
    lab_course = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 1, 2, "lab", 2, "Учебные лабораторные"
    )
    first = service.courses.add_groups(
        lab_course.id, {students[0].group_id}, "Первая", {students[0].student_id}
    )
    service.courses.add_groups(
        lab_course.id, {students[1].group_id}, "Вторая", {students[1].student_id}
    )
    service.courses.save_topic(lab_course.id, 1, "Лабораторная тема")
    occurrence = service.courses.schedule(lab_course.id, first.id, 1, PAST, 3)
    result = course_absences(service, lab_course.id, now=NOW)
    assert result.missed_topics[students[0].student_id] == {occurrence.topic_id}
    assert not result.missed_topics[students[1].student_id]


def test_frozen_empty_lecture_uses_enrollment_not_arrivals(lab):
    service, course, roster, students, _ = lab
    occurrence = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    block = freeze_legacy(service, occurrence, [])
    service.redesign.create_manual_batch(
        {(students[0].student_id, block.pair_ids[0])}, "Учебное посещение"
    )
    result = course_absences(service, course.id, now=NOW)
    assert not result.incomplete
    assert result.expected_topics[students[0].student_id] == {occurrence.topic_id}
    assert result.expected_topics[students[1].student_id] == {occurrence.topic_id}
    assert not result.missed_topics[students[0].student_id]
    assert result.missed_topics[students[1].student_id] == {occurrence.topic_id}


def test_frozen_empty_seminar_uses_assigned_line_and_preserves_nonempty_override(seminar):
    service, course, roster, students, _ = seminar
    occurrence = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    freeze_legacy(service, occurrence, [])
    result = course_absences(service, course.id, now=NOW)
    assert not result.incomplete
    assert all(v == {occurrence.topic_id} for v in result.missed_topics.values())
    freeze_legacy(service, occurrence, [students[0].student_id])
    result = course_absences(service, course.id, now=NOW)
    assert result.missed_topics[students[0].student_id] == {occurrence.topic_id}
    assert not result.missed_topics[students[1].student_id]


def test_new_lecture_has_all_cohort_without_enrollment_writes(lab):
    service, base, _, students, groups = lab
    other_year = service.redesign.save_year(2027)
    other_group = service.redesign.save_group(other_year.id, groups[0].specialty_id, 2, 1)
    third_year = service.redesign.save_group(base.academic_year_id, groups[0].specialty_id, 3, 1)
    new = service.courses.save_course(
        base.discipline_id, base.academic_year_id, 2, 2, "lecture", 1, "Новый общий поток"
    )
    before = service.repository.rd_revision()
    before_rosters = tuple(service.repository.rd_rosters())
    assert {s.student_id for s in service.courses.list_students(new.id)} == {
        s.student_id for s in students
    }
    assert not service.courses.list_rosters(new.id)
    assert service.repository.rd_revision() == before
    assert tuple(service.repository.rd_rosters()) == before_rosters
    stream = service.courses.lecture_roster(new.id)
    assert stream.mode == "groups" and set(stream.group_ids) == {g.id for g in groups}
    assert other_group.id not in stream.group_ids and third_year.id not in stream.group_ids
    assert service.courses.lecture_roster(new.id).id == stream.id
    occurrence = service.courses.save_topic(new.id, 1, "Новая тема")
    lesson = service.courses.schedule(new.id, stream.id, occurrence.number, PAST, 3)
    block = service.redesign.get_block(lesson.block_id)
    assert expected_student_ids(service, new.id, block.id) == {s.student_id for s in students}


def test_assign_existing_legacy_line_preserves_identity_and_all_facts(seminar):
    service, course, original, students, groups = seminar
    occurrence = service.courses.schedule(course.id, original.id, 1, PAST, 1)
    block = freeze_legacy(service, occurrence, [])
    unassign_occurrence(service, course, occurrence)
    block = service.redesign.get_block(block.id)
    legacy = service.redesign._roster(block.roster_id)
    service.redesign.create_manual_batch(
        {(students[0].student_id, block.pair_ids[0])}, "Факт до назначения"
    )
    tables = (
        "lessons",
        "lesson_roster",
        "lesson_context",
        "scans",
        "cards",
        "binding_changes",
        "manual_batches",
        "manual_evidence",
        "card_evidence",
        "registration_sessions",
        "session_target_pairs",
        "roster_adjustments",
        "scheduled_blocks",
        "course_lessons",
    )

    def facts():
        return {
            table: [
                tuple(row)
                for row in service.repository._connection.execute(
                    f"SELECT * FROM {table} ORDER BY 1"
                )
            ]
            for table in tables
        }

    before = facts()
    assigned = service.courses.assign_groups(course.id, legacy.id, {groups[1].id})
    assert assigned == replace(legacy, mode="groups", group_ids=(groups[1].id,))
    assert facts() == before
    expected = expected_student_ids(service, course.id, block.id)
    target = next(s for s in service.courses.list_students(course.id) if s.group_id == groups[1].id)
    assert expected == {target.student_id}
    query = course_absences(service, course.id, now=NOW)
    assert not query.incomplete and query.missed_topics[target.student_id] == {occurrence.topic_id}
    assert not query.missed_topics[students[0].student_id]
    assert groups[1].id in service.redesign.get_discipline_groups(
        course.discipline_id, course.academic_year_id
    )


def test_assign_line_validates_course_year_study_and_specialty_group_identity(seminar):
    service, course, roster, students, groups = seminar
    headers = [
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    ]
    service.redesign.apply_import(
        service.redesign.preview_import(
            headers,
            [["4", "Учебный Иной", "Другая специальность", "Профиль", "2", "1", "0004"]],
            2026,
        )
    )
    context = next(
        s
        for s in service.redesign.list_student_contexts(course.academic_year_id)
        if s.record_book_number == "0004"
    )
    assigned = service.courses.assign_groups(course.id, roster.id, {context.group_id})
    assert assigned.group_ids == (context.group_id,)
    assert {s.student_id for s in service.courses.list_students(course.id)} == {context.student_id}
    other_year = service.redesign.save_year(2027)
    wrong_year = service.redesign.save_group(other_year.id, groups[0].specialty_id, 2, 1)
    wrong_study = service.redesign.save_group(course.academic_year_id, groups[0].specialty_id, 3, 1)
    other = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 2, 2, "seminar", 1, "Иной курс"
    )
    for course_id, keys in (
        (course.id, set()),
        (course.id, {wrong_year.id}),
        (course.id, {wrong_study.id}),
        (other.id, {groups[0].id}),
    ):
        before = service.repository.rd_revision()
        with pytest.raises(AttendanceError):
            service.courses.assign_groups(course_id, roster.id, keys)
        assert service.repository.rd_revision() == before
        assert service.redesign._roster(roster.id) == assigned


def test_shared_expected_membership_rejects_other_course_and_deleted_blocks(lab):
    service, course, roster, _, _ = lab
    occurrence = service.courses.schedule(course.id, roster.id, 1, PAST, 1)
    block = service.redesign.get_block(occurrence.block_id)
    other = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 2, 2, "lecture", 1, "Другой курс"
    )
    with pytest.raises(AttendanceError):
        expected_student_ids(service, other.id, block.id)
    service.lifecycle.delete_lesson(block.pair_ids[0], "Учебное удаление")
    with pytest.raises(AttendanceError):
        expected_student_ids(service, course.id, block.id)
