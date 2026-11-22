"""Reversible lesson removal without changing original facts or identities."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LessonDeletion:
    id: int
    lesson_ids: tuple[int, ...]
    deleted_at: datetime
    reason: str
    restored_at: datetime | None = None
