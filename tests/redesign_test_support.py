"""Independent synthetic V1 fixture; deliberately never opens application data."""

import sqlite3
from pathlib import Path

V1_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE disciplines (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '');
CREATE TABLE lessons (id INTEGER PRIMARY KEY, discipline_id INTEGER NOT NULL REFERENCES
 disciplines(id), starts_at TEXT NOT NULL, ends_at TEXT NOT NULL CHECK(ends_at > starts_at),
 timezone TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', room TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL CHECK(state IN ('planned', 'open', 'completed')),
 roster_fixed INTEGER NOT NULL CHECK(roster_fixed IN (0, 1)));
CREATE UNIQUE INDEX single_open_lesson ON lessons(state) WHERE state = 'open';
CREATE INDEX lessons_by_discipline ON lessons(discipline_id, starts_at);
CREATE TABLE students (id TEXT PRIMARY KEY, full_name TEXT NOT NULL,
 student_group TEXT NOT NULL DEFAULT '');
CREATE TABLE discipline_roster (discipline_id INTEGER NOT NULL REFERENCES disciplines(id),
 student_id TEXT NOT NULL REFERENCES students(id), PRIMARY KEY(discipline_id, student_id));
CREATE TABLE lesson_roster (lesson_id INTEGER NOT NULL REFERENCES lessons(id),
 student_id TEXT NOT NULL REFERENCES students(id), PRIMARY KEY(lesson_id, student_id));
CREATE TABLE cards (uid TEXT PRIMARY KEY, student_id TEXT REFERENCES students(id));
CREATE TABLE scans (lesson_id INTEGER NOT NULL REFERENCES lessons(id),
 uid TEXT NOT NULL REFERENCES cards(uid), first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 PRIMARY KEY(lesson_id, uid));
CREATE INDEX scans_by_uid ON scans(uid);
CREATE TABLE binding_changes (id INTEGER PRIMARY KEY, uid TEXT NOT NULL REFERENCES cards(uid),
 previous_student_id TEXT REFERENCES students(id), student_id TEXT NOT NULL REFERENCES
 students(id), changed_at TEXT NOT NULL, correction INTEGER NOT NULL, scope TEXT NOT NULL);
PRAGMA user_version = 1;
"""

LEGACY_FIRST = "2026-09-07T06:00:00.000000+00:00"
LEGACY_LAST = "2026-09-07T06:04:00.000000+00:00"
LEGACY_UID = "0000AABB"
LEGACY_STUDENT = "synthetic-legacy-id"


def create_v1_database(path: Path) -> None:
    """Create only a caller-supplied tmp_path fixture, including fixed-empty history."""
    with sqlite3.connect(path) as connection:
        connection.executescript(V1_SCHEMA)
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("application", "attendance-lab"),
                ("mode", "real"),
            ],
        )
        connection.execute("INSERT INTO disciplines VALUES (17, 'Синтетическая физика', '')")
        connection.executemany(
            "INSERT INTO lessons VALUES (?, 17, ?, ?, 'Europe/Minsk', '', '', 'completed', 1)",
            [
                (41, LEGACY_FIRST, "2026-09-07T07:20:00.000000+00:00"),
                (42, "2026-09-07T08:00:00.000000+00:00", "2026-09-07T09:20:00.000000+00:00"),
            ],
        )
        connection.execute(
            "INSERT INTO students VALUES (?, 'Тестов Тест', 'old opaque group')", (LEGACY_STUDENT,)
        )
        connection.execute("INSERT INTO discipline_roster VALUES (17, ?)", (LEGACY_STUDENT,))
        connection.execute("INSERT INTO lesson_roster VALUES (41, ?)", (LEGACY_STUDENT,))
        connection.execute("INSERT INTO cards VALUES (?, ?)", (LEGACY_UID, LEGACY_STUDENT))
        connection.execute(
            "INSERT INTO scans VALUES (41, ?, ?, ?)", (LEGACY_UID, LEGACY_FIRST, LEGACY_LAST)
        )
        connection.execute(
            "INSERT INTO binding_changes VALUES (9, ?, NULL, ?, ?, 0, 'all_history')",
            (LEGACY_UID, LEGACY_STUDENT, LEGACY_FIRST),
        )


def logical_rows(path: Path, tables: tuple[str, ...]) -> dict[str, list[tuple]]:
    """Compare all values, including IDs/timestamps, without logging fixture rows."""
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1, 2').fetchall()
            for table in tables
        }
