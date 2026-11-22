"""Transactional whole-block removal and restoration of the same original lessons."""

from datetime import UTC, datetime
from typing import Protocol, cast

from attendance_lab.domain.lesson_lifecycle import LessonDeletion
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.rules import required_text


class LessonLifecycleStore(Protocol):
    def ll_deleted_lesson_ids(self) -> set[int]: ...
    def ll_deletions(self) -> list[LessonDeletion]: ...
    def ll_delete(self, lesson_ids: set[int], at: datetime, reason: str) -> LessonDeletion: ...
    def ll_restore(self, batch_id: int, at: datetime) -> None: ...


class LessonLifecycle:
    def __init__(self, service):
        self.service = service
        self.store = cast(LessonLifecycleStore, service.repository)

    def deleted_ids(self):
        return self.store.ll_deleted_lesson_ids()

    def require_live(self, lesson_id):
        if lesson_id in self.deleted_ids():
            raise AttendanceError(
                "Занятие удалено из расписания. Сначала восстановите его через «Удалённые занятия»."
            )

    def list_deletions(self, *, include_restored=False):
        return [d for d in self.store.ll_deletions() if include_restored or d.restored_at is None]

    def target_ids(self, lesson_id):
        self.service.get_lesson(lesson_id)
        block = next(
            (b for b in self.service.redesign.store.rd_blocks() if lesson_id in b.pair_ids), None
        )
        return set(block.pair_ids) if block else {lesson_id}

    def delete_lesson(self, lesson_id, reason):
        reason = required_text(reason, "Причина удаления")
        with self.service.repository.transaction():
            targets = self.target_ids(lesson_id)
            if targets.intersection(self.deleted_ids()):
                raise AttendanceError("Занятие уже удалено.")
            if any(self.service.get_lesson(key).state == "open" for key in targets) or any(
                s.closed_at is None and targets.intersection(s.target_pair_ids)
                for s in self.service.redesign.store.rd_sessions()
            ):
                raise AttendanceError(
                    "Сначала завершите регистрацию этого занятия. Активную регистрацию удалить нельзя."
                )
            return self.store.ll_delete(targets, datetime.now(UTC), reason)

    def restore_deletion(self, deletion_id):
        with self.service.repository.transaction():
            deletion = next((d for d in self.list_deletions() if d.id == deletion_id), None)
            if deletion is None:
                raise AttendanceError("Удаление не найдено или уже отменено.")
            self.store.ll_restore(deletion.id, datetime.now(UTC))

    def describe(self, lesson_id):
        from zoneinfo import ZoneInfo

        ids = self.target_ids(lesson_id)
        lessons = [self.service.get_lesson(key) for key in ids]
        first, last = (
            min(lessons, key=lambda lesson: lesson.starts_at),
            max(lessons, key=lambda lesson: lesson.ends_at),
        )
        zone = ZoneInfo(first.timezone)
        name = next(d.name for d in self.service.list_disciplines() if d.id == first.discipline_id)
        return f"{name} · {first.title or 'Занятие'} · {first.starts_at.astimezone(zone):%d.%m.%Y %H:%M}–{last.ends_at.astimezone(zone):%d.%m.%Y %H:%M}"
