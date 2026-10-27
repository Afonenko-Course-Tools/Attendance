"""Immutable catalog, scheduling and attendance evidence values."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime


def display_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def name_key(value: str) -> str:
    return display_name(value).casefold()


@dataclass(frozen=True, slots=True)
class AcademicYear:
    id: int
    start_year: int


@dataclass(frozen=True, slots=True)
class Specialty:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Profile:
    id: int
    specialty_id: int
    name: str


@dataclass(frozen=True, slots=True)
class AcademicGroup:
    id: int
    academic_year_id: int
    specialty_id: int
    course: int
    number: int
    variant_key: str = ""
    specialty_name: str = ""


@dataclass(frozen=True, slots=True)
class StudentContext:
    student_id: str
    full_name: str
    record_book_number: str = ""
    academic_year_id: int | None = None
    group_id: int | None = None
    group_number: int | None = None
    course: int | None = None
    specialty: str = ""
    profile: str = ""
    subgroup: str = ""


@dataclass(frozen=True, slots=True)
class ImportRow:
    source_row: int
    ordinal: int
    full_name: str
    specialty: str
    profile: str
    course: int
    group_number: int
    record_book_number: str


@dataclass(frozen=True, slots=True)
class ImportPlan:
    rows: tuple[ImportRow, ...]
    errors: tuple[str, ...]
    changes: tuple[str, ...]
    revision: str
    academic_year_start: int
    file_hash: str
    specialty_aliases: tuple[tuple[str, int], ...] = ()
    profile_aliases: tuple[tuple[int, str, int], ...] = ()
    group_choices: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class TeachingRoster:
    id: int
    discipline_id: int
    academic_year_id: int
    semester: int
    name: str
    kind: str
    duration: int
    mode: str
    group_ids: tuple[int, ...]
    student_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduledBlock:
    id: int
    roster_id: int
    date: date
    start_slot: int
    pair_ids: tuple[int, ...]
    title: str = ""
    room: str = ""
    timezone: str = "Europe/Minsk"


@dataclass(frozen=True, slots=True)
class RegistrationSession:
    id: int
    block_id: int
    target_pair_ids: tuple[int, ...]
    opened_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ManualBatch:
    id: int
    targets: tuple[tuple[str, int], ...]
    reason: str
    created_at: datetime
    revoked_at: datetime | None = None
    session_id: int | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str
    pair_id: int
    student_id: str | None
    at: datetime
    uid: str | None = None
    session_id: int | None = None
    batch_id: int | None = None
    active: bool = True
