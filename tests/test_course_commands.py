"""Availability follows captured identities and current application facts, without Qt."""

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from attendance_lab.application.course_commands import (
    CourseCommandContext,
    course_command_availability,
)
from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def course(tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "commands.sqlite3", mode="demo"))
    headers = [
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    ]
    rows = [["1", "Учебный Первый", "Тест", "Профиль", "2", "1", "0001"]]
    service.redesign.apply_import(service.redesign.preview_import(headers, rows, 2026))
    group = service.redesign.list_groups()[0]
    discipline = service.save_discipline("Учебная дисциплина")
    course = service.courses.save_course(
        discipline.id, group.academic_year_id, 1, 2, "seminar", 1, "Учебный курс"
    )
    roster = service.courses.add_groups(course.id, {group.id})
    topic = service.courses.save_topic(course.id, 1, "Учебная тема")
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    student = service.courses.list_students(course.id)[0].student_id
    context = CourseCommandContext(
        course.id, lesson.id, topic.id, roster.id, frozenset({(student, topic.id)}), True
    )
    yield service, course, lesson, context
    service.close()


def test_no_course_disables_contextual_commands_and_snapshot_is_immutable(course):
    service, _, _, _ = course
    states = course_command_availability(service, CourseCommandContext(None))
    assert {name for name, state in states.items() if state.enabled} == {"new_course"}
    assert not states["subgroup"].visible
    with pytest.raises(TypeError):
        states["view"] = states["new_course"]
    with pytest.raises(FrozenInstanceError):
        states["view"].enabled = True


def test_selected_ids_enable_commands_and_stale_ids_do_not(course):
    service, _, _, context = course
    states = course_command_availability(service, context)
    assert all(
        states[name].enabled for name in ("view", "move", "delete", "edit_topic", "mark", "export")
    )
    assert not states["recurrence"].enabled
    stale = replace(context, lesson_id=-1, topic_id=-1, roster_id=-1)
    states = course_command_availability(service, stale)
    assert all(
        not states[name].enabled for name in ("view", "move", "delete", "edit_topic", "edit_groups")
    )


def test_active_registration_prevents_move_and_deletion_without_side_effects(course):
    service, _, lesson, context = course
    session = service.courses.open_registration(lesson.id)
    states = course_command_availability(service, context)
    assert not states["move"].enabled
    assert not states["delete"].enabled
    assert "завершите" in states["delete"].hint
    assert service.redesign.get_session(session.id).closed_at is None


def test_attendance_refresh_disables_repeat_but_does_not_change_old_snapshot(course):
    service, semester, lesson, context = course
    before = course_command_availability(service, context)
    service.courses.mark_topics(
        semester.id, set(context.selected_cells), {lesson.topic_id: lesson.id}, "Учебная отметка"
    )
    after = course_command_availability(service, context)
    assert before["mark"].enabled
    assert not after["mark"].enabled
    assert after["mark"].hint == "Посещение уже отмечено"
    assert not after["move"].enabled


def test_unscheduled_topic_and_empty_selection_do_not_grant_mark(course):
    service, semester, _, context = course
    topic = service.courses.save_topic(semester.id, 2, "Без занятия")
    student = next(iter(context.selected_cells))[0]
    for cells in (frozenset(), frozenset({(student, topic.id)})):
        states = course_command_availability(service, replace(context, selected_cells=cells))
        assert not states["mark"].enabled


def test_lecture_has_no_group_editing_commands(course):
    service, semester, _, context = course
    lecture = service.courses.save_course(
        semester.discipline_id, semester.academic_year_id, 1, 2, "lecture", 1, "Учебная лекция"
    )
    states = course_command_availability(service, replace(context, course_id=lecture.id))
    assert states["schedule"].enabled
    assert all(not states[name].visible for name in ("groups", "subgroup", "edit_groups"))
    assert not states["view"].enabled


def test_deletion_and_restoration_change_availability_by_stable_id(course):
    service, _, lesson, context = course
    block = service.redesign.get_block(lesson.block_id)
    deletion = service.lifecycle.delete_lesson(block.pair_ids[0], "Учебное удаление")
    states = course_command_availability(service, context)
    assert states["restore"].enabled
    assert not states["view"].enabled
    assert not states["mark"].enabled
    service.lifecycle.restore_deletion(deletion.id)
    restored = course_command_availability(service, context)
    assert not restored["restore"].enabled
    assert restored["view"].enabled
    assert restored["mark"].enabled
