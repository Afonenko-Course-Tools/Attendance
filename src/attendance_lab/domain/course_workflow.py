"""Semester course identities, shared programme and scheduled occurrences."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class SemesterCourse:
    id: int
    discipline_id: int
    academic_year_id: int
    semester: int
    study_course: int
    kind: str
    duration: int
    reports_enabled: bool
    name: str


@dataclass(frozen=True, slots=True)
class CourseTopic:
    id: int
    course_id: int
    number: int
    title: str


@dataclass(frozen=True, slots=True)
class CourseLesson:
    id: int
    course_id: int
    topic_id: int
    block_id: int
    series_id: int | None = None
    sequence_index: int | None = None


@dataclass(frozen=True, slots=True)
class CourseSeries:
    id: int
    course_id: int
    roster_id: int
    first_date: date
    start_slot: int
    interval_days: int
    topic_number: int
    room: str
    timezone: str
