"""Known missed topics and explicitly unresolved historical expectations.

An attendance observation proves presence, never membership. Frozen lesson_roster
IDs remain usable when the optional rich lesson_context snapshot is absent.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from attendance_lab.domain.models import AttendanceError

if TYPE_CHECKING:
    from attendance_lab.application.service import AttendanceService


@dataclass(frozen=True, slots=True)
class CourseAbsences:
    expected_topics: Mapping[str, frozenset[int]]
    missed_topics: Mapping[str, frozenset[int]]
    unresolved_lesson_ids: tuple[int, ...]
    unresolved_topic_ids: frozenset[int]

    @property
    def incomplete(self) -> bool:
        return bool(self.unresolved_lesson_ids)


def _expected_ids(service, course, block, pairs, students, assigned_rosters):
    if course.kind == "lecture":
        return frozenset(students)
    fixed = [pair for pair in pairs if pair.roster_fixed]
    ids = set().union(*(service.repository.get_lesson_roster(pair.id) for pair in fixed))
    if not ids:
        assigned = assigned_rosters.get(block.roster_id)
        ids = (
            {context.student_id for context in service.redesign._roster_contexts(assigned)}
            if assigned is not None
            else set()
        )
    return frozenset(ids)


def expected_student_ids(
    service: AttendanceService, course_id: int, block_id: int
) -> frozenset[str]:
    """Expected block membership shared by the lesson screen and absence query.

    Read only: cohort membership for lectures; a nonempty historical ID snapshot
    or the explicitly assigned teaching line for other formats. No scan inference.
    """
    with service.repository.transaction(write=False):
        course = service.courses.get_course(course_id)
        if block_id not in {lesson.block_id for lesson in service.courses.list_lessons(course_id)}:
            raise AttendanceError("Занятие не относится к выбранному курсу или удалено.")
        block = service.redesign.get_block(block_id)
        pairs = [service.get_lesson(pair_id) for pair_id in block.pair_ids]
        students = {student.student_id for student in service.courses.list_students(course_id)}
        rosters = {roster.id: roster for roster in service.courses.list_rosters(course_id)}
        return _expected_ids(service, course, block, pairs, students, rosters)


def course_absences(
    service: AttendanceService,
    course_id: int,
    projection: set[tuple[str, int]] | None = None,
    *,
    now: datetime | None = None,
) -> CourseAbsences:
    """Read course enrollment and assigned lesson groups without repairing history.

    Lectures require the enrolled course stream for every conducted topic, even
    when a legacy lesson snapshot is empty or narrower. For other formats a
    nonempty frozen ID snapshot preserves explicit historical membership; an
    empty legacy snapshot can use the lesson's explicitly assigned group/student
    roster. Arrivals never establish enrollment. A genuinely unassigned lesson
    remains unresolved. Different occurrences of one topic close the same topic.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise AttendanceError("Для расчёта пропусков необходимо время с часовым поясом.")
    with service.repository.transaction(write=False):
        course = service.courses.get_course(course_id)
        students = {s.student_id for s in service.courses.list_students(course_id)}
        present = (
            projection
            if projection is not None
            else service.courses.attendance_projection(course_id)
        )
        assigned_rosters = {roster.id: roster for roster in service.courses.list_rosters(course_id)}
        expected: dict[str, set[int]] = {student: set() for student in students}
        unresolved_lessons: list[int] = []
        unresolved_topics: set[int] = set()
        for occurrence in service.courses.list_lessons(course_id):
            block = service.redesign.get_block(occurrence.block_id)
            pairs = [service.get_lesson(pair_id) for pair_id in block.pair_ids]
            if max(pair.ends_at for pair in pairs) > now:
                continue
            ids = _expected_ids(service, course, block, pairs, students, assigned_rosters)
            if not ids:
                unresolved_lessons.append(occurrence.id)
                unresolved_topics.add(occurrence.topic_id)
                continue
            for student in ids.intersection(students):
                expected[student].add(occurrence.topic_id)
        frozen_expected = {student: frozenset(topics) for student, topics in expected.items()}
        missing = {
            student: frozenset(topic for topic in topics if (student, topic) not in present)
            for student, topics in expected.items()
        }
        return CourseAbsences(
            MappingProxyType(frozen_expected),
            MappingProxyType(missing),
            tuple(unresolved_lessons),
            frozenset(unresolved_topics),
        )
