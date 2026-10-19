"""Framework-independent data shared by application ports and views."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

LessonState = Literal["planned", "open", "completed"]
CellState = Literal["present", "missing", "expected", "pending", "outside"]


@dataclass(frozen=True, slots=True)
class Discipline:
    id: int
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class Lesson:
    id: int
    discipline_id: int
    starts_at: datetime
    ends_at: datetime
    timezone: str
    title: str = ""
    room: str = ""
    state: LessonState = "planned"
    roster_fixed: bool = False


@dataclass(frozen=True, slots=True)
class Student:
    id: str
    full_name: str
    group: str = ""


@dataclass(frozen=True, slots=True)
class CardInfo:
    uid: str
    student_id: str | None
    full_name: str | None
    lesson_count: int


@dataclass(frozen=True, slots=True)
class LogRow:
    uid: str
    student_id: str | None
    full_name: str | None
    first_seen: datetime
    last_seen: datetime
    outside_roster: bool = False


@dataclass(frozen=True, slots=True)
class LessonCounts:
    expected_students: int
    unique_cards: int
    identified_students: int
    unidentified_cards: int


@dataclass(frozen=True, slots=True)
class PublicResult:
    """Deliberately cannot carry UID, ATR, exceptions or paths."""

    kind: Literal["success", "repeat", "error", "idle", "closed"]
    message: str
    full_name: str | None = None
    at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AttendanceCell:
    state: CellState
    first_seen: datetime | None = None
    outside_roster: bool = False


@dataclass(frozen=True, slots=True)
class AttendanceRow:
    student: Student
    cells: tuple[AttendanceCell, ...]


@dataclass(frozen=True, slots=True)
class AttendanceReport:
    lessons: tuple[Lesson, ...]
    rows: tuple[AttendanceRow, ...]
    unknown_count: int = 0
    discipline_name: str = ""


class AttendanceError(ValueError):
    """An operator-readable validation error, never forwarded publicly."""
