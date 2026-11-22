"""Read-only course command availability shared by presentation adapters."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from attendance_lab.application.service import AttendanceService


@dataclass(frozen=True, slots=True)
class CourseCommandContext:
    course_id: int | None
    lesson_id: int | None = None
    topic_id: int | None = None
    roster_id: int | None = None
    selected_cells: frozenset[tuple[str, int]] = frozenset()
    has_journal_rows: bool = False


@dataclass(frozen=True, slots=True)
class CommandAvailability:
    enabled: bool
    visible: bool = True
    hint: str | None = None


COMMANDS = (
    "new_course",
    "settings",
    "copy",
    "written",
    "add_topic",
    "add_topics",
    "edit_topic",
    "groups",
    "subgroup",
    "edit_groups",
    "schedule",
    "view",
    "move",
    "recurrence",
    "delete",
    "restore",
    "lesson_topic",
    "mark",
    "export",
)


def journal_mark_availability(
    cells: Iterable[tuple[str, int]],
    scheduled_topics: Iterable[int],
    present: Iterable[tuple[str, int]],
) -> CommandAvailability:
    """One availability rule for toolbar, keyboard and direct journal activation."""
    selected = frozenset(cells)
    scheduled = frozenset(scheduled_topics)
    remaining = selected.difference(present)
    enabled = bool(remaining) and all(topic_id in scheduled for _, topic_id in selected)
    return CommandAvailability(
        enabled,
        hint="Поставить посещение · Enter или Пробел; также можно дважды щёлкнуть ячейку"
        if enabled
        else "Посещение уже отмечено"
        if selected and not remaining
        else "Выберите ячейки тем, для которых есть занятие в расписании",
    )


def course_command_availability(
    service: AttendanceService, context: CourseCommandContext
) -> Mapping[str, CommandAvailability]:
    """Project one consistent read snapshot; write scenarios still enforce invariants.

    The context contains identities captured by the view, never row positions or
    widgets. A selection that disappeared from the course grants no command.
    """
    with service.repository.transaction(write=False):
        valid = context.course_id is not None
        result = {name: CommandAvailability(valid or name == "new_course") for name in COMMANDS}
        result["restore"] = CommandAvailability(bool(service.lifecycle.list_deletions()))
        if context.course_id is None:
            for name in ("groups", "subgroup", "edit_groups"):
                result[name] = CommandAvailability(False, visible=False)
            return MappingProxyType(result)

        api = service.courses
        course = api.get_course(context.course_id)
        rosters = api.list_rosters(course.id)
        lessons = api.list_lessons(course.id)
        topics = {topic.id for topic in api.list_topics(course.id)}
        grouped = course.kind != "lecture"
        for name in ("groups", "subgroup"):
            result[name] = CommandAvailability(True, visible=grouped)
        result["edit_groups"] = CommandAvailability(
            grouped and context.roster_id in {roster.id for roster in rosters}, visible=grouped
        )
        result["edit_topic"] = CommandAvailability(context.topic_id in topics)
        result["schedule"] = CommandAvailability(
            bool(api.list_students(course.id) if not grouped else rosters)
        )
        lesson = next((item for item in lessons if item.id == context.lesson_id), None)
        for name in ("view", "move", "delete", "lesson_topic"):
            result[name] = CommandAvailability(lesson is not None)
        result["recurrence"] = CommandAvailability(
            lesson is not None and lesson.series_id is not None
        )
        if lesson is not None:
            block = service.redesign.get_block(lesson.block_id)
            pairs = [service.get_lesson(pair_id) for pair_id in block.pair_ids]
            can_delete = not any(pair.state == "open" for pair in pairs)
            move_reason = service.redesign.schedule_edit_reason(block)
            result["move"] = CommandAvailability(
                not move_reason, hint=move_reason or "Изменить дату и время выбранного занятия"
            )
            result["delete"] = CommandAvailability(
                can_delete,
                hint="Удалить выбранное занятие"
                if can_delete
                else "Сначала завершите регистрацию занятия",
            )

        scheduled_topics = {item.topic_id for item in lessons}
        present = api.attendance_projection(course.id) if context.selected_cells else set()
        result["mark"] = journal_mark_availability(
            context.selected_cells, scheduled_topics, present
        )
        result["export"] = CommandAvailability(context.has_journal_rows)
        return MappingProxyType(result)
