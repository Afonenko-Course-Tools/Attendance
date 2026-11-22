"""Reversible lesson hiding. All original rows and attendance evidence remain intact."""

import sqlite3
from datetime import UTC, datetime

from attendance_lab.domain.models import AttendanceError

LIFECYCLE_SCHEMA = """
CREATE TABLE deletion_batches (
 id INTEGER PRIMARY KEY,
 deleted_at TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 restored_at TEXT CHECK(restored_at IS NULL OR restored_at>=deleted_at)
);
CREATE TABLE deleted_lessons (
 batch_id INTEGER NOT NULL REFERENCES deletion_batches(id),
 lesson_id INTEGER NOT NULL REFERENCES lessons(id),
 PRIMARY KEY(batch_id,lesson_id)
);
CREATE INDEX deleted_lessons_by_lesson ON deleted_lessons(lesson_id);
CREATE TRIGGER no_duplicate_active_lesson_deletion BEFORE INSERT ON deleted_lessons
 WHEN EXISTS(SELECT 1 FROM deleted_lessons dl JOIN deletion_batches db ON db.id=dl.batch_id
 WHERE dl.lesson_id=NEW.lesson_id AND db.restored_at IS NULL)
 BEGIN SELECT RAISE(ABORT,'lesson already hidden'); END;
"""
LIFECYCLE_TABLE_COLUMNS = {
    "deletion_batches": {"id", "deleted_at", "reason", "restored_at"},
    "deleted_lessons": {"batch_id", "lesson_id"},
}


def _stamp(at: datetime) -> str:
    if at.tzinfo is None or at.utcoffset() is None:
        raise AttendanceError("Укажите время действия с часовым поясом.")
    return at.astimezone(UTC).isoformat(timespec="microseconds")


class LessonLifecycleSQLiteMixin:
    _connection: sqlite3.Connection

    def ll_deleted_lesson_ids(self) -> set[int]:
        return {
            r[0]
            for r in self._connection.execute(
                "SELECT dl.lesson_id FROM deleted_lessons dl JOIN deletion_batches db "
                "ON db.id=dl.batch_id WHERE db.restored_at IS NULL"
            )
        }

    def ll_deletions(self):
        from attendance_lab.domain.lesson_lifecycle import LessonDeletion

        return [
            LessonDeletion(
                r[0],
                tuple(
                    x[0]
                    for x in self._connection.execute(
                        "SELECT lesson_id FROM deleted_lessons WHERE batch_id=? ORDER BY lesson_id",
                        (r[0],),
                    )
                ),
                datetime.fromisoformat(r[1]),
                r[2],
                datetime.fromisoformat(r[3]) if r[3] else None,
            )
            for r in self._connection.execute(
                "SELECT id,deleted_at,reason,restored_at FROM deletion_batches ORDER BY id"
            )
        ]

    def ll_delete(self, lesson_ids: set[int], at: datetime, reason: str):
        if not lesson_ids or any(type(key) is not int or key <= 0 for key in lesson_ids):
            raise AttendanceError("Выберите занятия для удаления.")
        if not reason.strip():
            raise AttendanceError("Укажите причину удаления.")
        if lesson_ids & self.ll_deleted_lesson_ids():
            raise AttendanceError("Некоторые занятия уже удалены; сначала восстановите их.")
        cursor = self._connection.execute(
            "INSERT INTO deletion_batches(deleted_at,reason) VALUES(?,?)",
            (_stamp(at), reason.strip()),
        )
        batch_id = cursor.lastrowid
        self._connection.executemany(
            "INSERT INTO deleted_lessons VALUES(?,?)",
            [(batch_id, key) for key in sorted(lesson_ids)],
        )
        return next(row for row in self.ll_deletions() if row.id == batch_id)

    def ll_restore(self, batch_id: int, at: datetime) -> None:
        cursor = self._connection.execute(
            "UPDATE deletion_batches SET restored_at=? WHERE id=? AND restored_at IS NULL",
            (_stamp(at), batch_id),
        )
        if cursor.rowcount != 1:
            raise AttendanceError("Удаление не найдено или уже отменено.")
