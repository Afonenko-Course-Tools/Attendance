"""Typed occurrence data shared by schedule pickers and grading lists."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

type LessonChoiceStatus = Literal["planned", "open", "completed", "deleted"]


@dataclass(frozen=True, slots=True)
class LessonChoice:
    id: int
    block_id: int
    roster_id: int
    start_slot: int
    starts_at: datetime
    ends_at: datetime
    timezone: str
    topic: str
    group: str
    status: LessonChoiceStatus

    @property
    def date(self) -> date:
        return self.starts_at.date()

    @property
    def time(self) -> str:
        return f"{self.starts_at:%H:%M}–{self.ends_at:%H:%M}"

    @property
    def deleted(self) -> bool:
        return self.status == "deleted"

    @property
    def label(self) -> str:
        return f"{self.starts_at:%d.%m.%Y %H:%M} · {self.topic} · {self.group}" + (
            " (удалено)" if self.deleted else ""
        )
