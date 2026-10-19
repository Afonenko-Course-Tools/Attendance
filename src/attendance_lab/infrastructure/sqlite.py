"""SQLite persistence with explicit transactions, schema versions and backups."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from attendance_lab.application.ports import CardRecord, ScanRecord
from attendance_lab.domain.models import AttendanceError, Discipline, Lesson, LessonState, Student

SCHEMA_VERSION = 1

# The SQL schema enforces storage invariants even when a caller has a programming error.
_SCHEMA_V1 = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE disciplines (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE lessons (
    id INTEGER PRIMARY KEY,
    discipline_id INTEGER NOT NULL REFERENCES disciplines(id),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL CHECK(ends_at > starts_at),
    timezone TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    room TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL CHECK(state IN ('planned', 'open', 'completed')),
    roster_fixed INTEGER NOT NULL CHECK(roster_fixed IN (0, 1))
);
CREATE UNIQUE INDEX single_open_lesson ON lessons(state) WHERE state = 'open';
CREATE INDEX lessons_by_discipline ON lessons(discipline_id, starts_at);
CREATE TABLE students (
    id TEXT PRIMARY KEY CHECK(length(trim(id)) > 0),
    full_name TEXT NOT NULL CHECK(length(trim(full_name)) > 0),
    student_group TEXT NOT NULL DEFAULT ''
);
CREATE TABLE discipline_roster (
    discipline_id INTEGER NOT NULL REFERENCES disciplines(id),
    student_id TEXT NOT NULL REFERENCES students(id),
    PRIMARY KEY(discipline_id, student_id)
);
CREATE TABLE lesson_roster (
    lesson_id INTEGER NOT NULL REFERENCES lessons(id),
    student_id TEXT NOT NULL REFERENCES students(id),
    PRIMARY KEY(lesson_id, student_id)
);
CREATE TABLE cards (
    uid TEXT PRIMARY KEY CHECK(length(uid) > 0 AND length(uid) % 2 = 0
        AND uid NOT GLOB '*[^0-9A-F]*'),
    student_id TEXT REFERENCES students(id)
);
CREATE TABLE scans (
    lesson_id INTEGER NOT NULL REFERENCES lessons(id),
    uid TEXT NOT NULL REFERENCES cards(uid),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL CHECK(last_seen >= first_seen),
    PRIMARY KEY(lesson_id, uid)
);
CREATE INDEX scans_by_uid ON scans(uid);
CREATE TABLE binding_changes (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL REFERENCES cards(uid),
    previous_student_id TEXT REFERENCES students(id),
    student_id TEXT NOT NULL REFERENCES students(id),
    changed_at TEXT NOT NULL,
    correction INTEGER NOT NULL CHECK(correction IN (0, 1)),
    scope TEXT NOT NULL CHECK(scope = 'all_history')
);
"""


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AttendanceError("Хранилище принимает только время с часовым поясом.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise AttendanceError("База содержит время без часового пояса.")
    return result.astimezone(UTC)


class SQLiteRepository:
    """One main-thread connection. The reader communicates through the controller queue."""

    def __init__(self, path: Path, *, mode: str = "real") -> None:
        if mode not in {"real", "demo"}:
            raise AttendanceError("Неизвестный режим базы данных.")
        self.path = Path(path).expanduser().resolve()
        self.mode = mode
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        self._connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        self._depth = 0
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._migrate(existed)
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
        except Exception:
            self._connection.close()
            self._closed = True
            raise

    def _migrate(self, existed: bool) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise AttendanceError("База создана более новой версией приложения.")
        if version == 0:
            tables = self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if tables:
                raise AttendanceError("Файл не является базой Attendance Lab известной версии.")
            if existed and self.path.stat().st_size:
                self._write_backup(self._safety_path("before-migration"))
            try:
                self._connection.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA_V1)
                self._connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('mode', ?), ('application', ?)",
                    (self.mode, "attendance-lab"),
                )
                self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        self._validate_database(self._connection)

    def _validate_database(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_VERSION:
            raise AttendanceError("Неподдерживаемая версия базы данных.")
        try:
            values = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
            if values.get("application") != "attendance-lab":
                raise AttendanceError("Файл не является базой Attendance Lab.")
            if values.get("mode") != self.mode:
                raise AttendanceError(
                    "Режимы базы и приложения различаются: "
                    "реальные и демонстрационные данные нельзя смешивать."
                )
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AttendanceError("Проверка целостности базы не пройдена.")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise AttendanceError("В базе повреждены связи между записями.")
            required_columns = {
                "disciplines": {"id", "name", "description"},
                "lessons": {
                    "id",
                    "discipline_id",
                    "starts_at",
                    "ends_at",
                    "timezone",
                    "title",
                    "room",
                    "state",
                    "roster_fixed",
                },
                "students": {"id", "full_name", "student_group"},
                "discipline_roster": {"discipline_id", "student_id"},
                "lesson_roster": {"lesson_id", "student_id"},
                "cards": {"uid", "student_id"},
                "scans": {"lesson_id", "uid", "first_seen", "last_seen"},
                "binding_changes": {
                    "id",
                    "uid",
                    "previous_student_id",
                    "student_id",
                    "changed_at",
                    "correction",
                    "scope",
                },
            }
            for table, columns in required_columns.items():
                found = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if not columns.issubset(found):
                    raise AttendanceError("Структура базы данных повреждена или неизвестна.")
        except sqlite3.DatabaseError as exc:
            raise AttendanceError("Не удалось проверить файл базы данных.") from exc

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[None]:
        if self._closed:
            raise AttendanceError("База данных уже закрыта.")
        depth = self._depth
        savepoint = f"attendance_{depth}"
        if depth:
            self._connection.execute(f"SAVEPOINT {savepoint}")
        else:
            self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        self._depth += 1
        try:
            yield
            if depth:
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self._connection.commit()
        except BaseException:
            if depth:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self._connection.rollback()
            raise
        finally:
            self._depth -= 1

    @staticmethod
    def _discipline(row: sqlite3.Row) -> Discipline:
        return Discipline(row["id"], row["name"], row["description"])

    @staticmethod
    def _lesson(row: sqlite3.Row) -> Lesson:
        return Lesson(
            row["id"],
            row["discipline_id"],
            _datetime(row["starts_at"]),
            _datetime(row["ends_at"]),
            row["timezone"],
            row["title"],
            row["room"],
            cast(LessonState, row["state"]),
            bool(row["roster_fixed"]),
        )

    @staticmethod
    def _student(row: sqlite3.Row) -> Student:
        return Student(row["id"], row["full_name"], row["student_group"])

    @staticmethod
    def _scan(row: sqlite3.Row) -> ScanRecord:
        return ScanRecord(
            row["lesson_id"],
            row["uid"],
            _datetime(row["first_seen"]),
            _datetime(row["last_seen"]),
        )

    def list_disciplines(self) -> list[Discipline]:
        return [
            self._discipline(row)
            for row in self._connection.execute("SELECT * FROM disciplines ORDER BY name, id")
        ]

    def get_discipline(self, discipline_id: int) -> Discipline | None:
        row = self._connection.execute(
            "SELECT * FROM disciplines WHERE id = ?", (discipline_id,)
        ).fetchone()
        return self._discipline(row) if row is not None else None

    def put_discipline(self, discipline: Discipline) -> Discipline:
        if discipline.id:
            self._connection.execute(
                "UPDATE disciplines SET name = ?, description = ? WHERE id = ?",
                (discipline.name, discipline.description, discipline.id),
            )
            return discipline
        cursor = self._connection.execute(
            "INSERT INTO disciplines(name, description) VALUES (?, ?)",
            (discipline.name, discipline.description),
        )
        assert cursor.lastrowid is not None
        return replace(discipline, id=cursor.lastrowid)

    def list_lessons(self, discipline_id: int | None = None) -> list[Lesson]:
        if discipline_id is None:
            rows = self._connection.execute("SELECT * FROM lessons ORDER BY starts_at, id")
        else:
            rows = self._connection.execute(
                "SELECT * FROM lessons WHERE discipline_id = ? ORDER BY starts_at, id",
                (discipline_id,),
            )
        return [self._lesson(row) for row in rows]

    def get_lesson(self, lesson_id: int) -> Lesson | None:
        row = self._connection.execute(
            "SELECT * FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        return self._lesson(row) if row is not None else None

    def put_lesson(self, lesson: Lesson) -> Lesson:
        values = (
            lesson.discipline_id,
            _timestamp(lesson.starts_at),
            _timestamp(lesson.ends_at),
            lesson.timezone,
            lesson.title,
            lesson.room,
            lesson.state,
            int(lesson.roster_fixed),
        )
        if lesson.id:
            self._connection.execute(
                "UPDATE lessons SET discipline_id=?, starts_at=?, ends_at=?, timezone=?, "
                "title=?, room=?, state=?, roster_fixed=? WHERE id=?",
                (*values, lesson.id),
            )
            return lesson
        cursor = self._connection.execute(
            "INSERT INTO lessons(discipline_id, starts_at, ends_at, timezone, title, "
            "room, state, roster_fixed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        assert cursor.lastrowid is not None
        return replace(lesson, id=cursor.lastrowid)

    def list_students(self) -> list[Student]:
        return [
            self._student(row)
            for row in self._connection.execute(
                "SELECT * FROM students ORDER BY full_name, student_group, id"
            )
        ]

    def get_student(self, student_id: str) -> Student | None:
        row = self._connection.execute(
            "SELECT * FROM students WHERE id = ?", (student_id,)
        ).fetchone()
        return self._student(row) if row is not None else None

    def put_student(self, student: Student) -> None:
        self._connection.execute(
            "INSERT INTO students(id, full_name, student_group) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name, "
            "student_group=excluded.student_group",
            (student.id, student.full_name, student.group),
        )

    def get_discipline_roster(self, discipline_id: int) -> set[str]:
        return {
            row[0]
            for row in self._connection.execute(
                "SELECT student_id FROM discipline_roster WHERE discipline_id=?", (discipline_id,)
            )
        }

    def put_discipline_roster(self, discipline_id: int, student_ids: set[str]) -> None:
        self._connection.execute(
            "DELETE FROM discipline_roster WHERE discipline_id=?", (discipline_id,)
        )
        self._connection.executemany(
            "INSERT INTO discipline_roster(discipline_id, student_id) VALUES (?, ?)",
            [(discipline_id, student_id) for student_id in sorted(student_ids)],
        )

    def get_lesson_roster(self, lesson_id: int) -> set[str]:
        return {
            row[0]
            for row in self._connection.execute(
                "SELECT student_id FROM lesson_roster WHERE lesson_id=?", (lesson_id,)
            )
        }

    def put_lesson_roster(self, lesson_id: int, student_ids: set[str]) -> None:
        self._connection.execute("DELETE FROM lesson_roster WHERE lesson_id=?", (lesson_id,))
        self._connection.executemany(
            "INSERT INTO lesson_roster(lesson_id, student_id) VALUES (?, ?)",
            [(lesson_id, student_id) for student_id in sorted(student_ids)],
        )

    def list_cards(self) -> list[CardRecord]:
        return [
            CardRecord(row["uid"], row["student_id"])
            for row in self._connection.execute("SELECT * FROM cards ORDER BY uid")
        ]

    def get_card(self, uid: str) -> CardRecord | None:
        row = self._connection.execute("SELECT * FROM cards WHERE uid=?", (uid,)).fetchone()
        return CardRecord(row["uid"], row["student_id"]) if row is not None else None

    def put_card(self, card: CardRecord) -> None:
        self._connection.execute(
            "INSERT INTO cards(uid, student_id) VALUES (?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET student_id=excluded.student_id",
            (card.uid, card.student_id),
        )

    def add_binding_change(
        self,
        uid: str,
        previous_student_id: str | None,
        student_id: str,
        changed_at: datetime,
        *,
        correction: bool,
    ) -> None:
        self._connection.execute(
            "INSERT INTO binding_changes(uid, previous_student_id, student_id, changed_at, "
            "correction, scope) VALUES (?, ?, ?, ?, ?, 'all_history')",
            (uid, previous_student_id, student_id, _timestamp(changed_at), int(correction)),
        )

    def list_scans(self, lesson_id: int | None = None) -> list[ScanRecord]:
        if lesson_id is None:
            rows = self._connection.execute("SELECT * FROM scans ORDER BY first_seen, uid")
        else:
            rows = self._connection.execute(
                "SELECT * FROM scans WHERE lesson_id=? ORDER BY first_seen, uid", (lesson_id,)
            )
        return [self._scan(row) for row in rows]

    def get_scan(self, lesson_id: int, uid: str) -> ScanRecord | None:
        row = self._connection.execute(
            "SELECT * FROM scans WHERE lesson_id=? AND uid=?", (lesson_id, uid)
        ).fetchone()
        return self._scan(row) if row is not None else None

    def put_scan(self, scan: ScanRecord) -> None:
        self._connection.execute(
            "INSERT INTO scans(lesson_id, uid, first_seen, last_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(lesson_id, uid) DO UPDATE SET last_seen=excluded.last_seen",
            (scan.lesson_id, scan.uid, _timestamp(scan.first_seen), _timestamp(scan.last_seen)),
        )

    def _safety_path(self, action: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return self.path.with_name(f"{self.path.stem}.{action}.{stamp}.sqlite3")

    def _different_path(self, path: Path) -> Path:
        result = Path(path).expanduser().resolve()
        reserved = {
            self.path,
            *(Path(str(self.path) + suffix) for suffix in ("-wal", "-shm", "-journal")),
        }
        if result in reserved or (result.exists() and result.samefile(self.path)):
            raise AttendanceError("Выберите файл, отличный от текущей базы данных.")
        return result

    def _write_backup(self, path: Path) -> None:
        if self._depth:
            raise AttendanceError("Резервная копия недоступна внутри транзакции.")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            destination = sqlite3.connect(temporary)
            try:
                self._connection.backup(destination)
            finally:
                destination.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def backup(self, path: Path) -> None:
        self._write_backup(self._different_path(path))

    def restore(self, path: Path) -> None:
        source_path = self._different_path(path)
        if not source_path.is_file():
            raise AttendanceError("Файл резервной копии не найден.")
        if self._depth:
            raise AttendanceError("Восстановление недоступно внутри транзакции.")
        source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            # Validate a stable source snapshot before changing any current data.
            source.execute("BEGIN")
            self._validate_database(source)
            self._write_backup(self._safety_path("before-restore"))
            source.backup(self._connection)
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
        except sqlite3.DatabaseError as exc:
            raise AttendanceError("Не удалось восстановить резервную копию.") from exc
        finally:
            source.close()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True
