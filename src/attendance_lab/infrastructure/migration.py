"""Private, offline migration rehearsal and exact-version rollback.

A rehearsal only opens the live source read-only. SQLite Backup API includes
committed WAL frames; no checkpoint, shutdown or upgrade is issued on the source.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from attendance_lab.application.redesign import SLOTS
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SCHEMA_VERSION, SQLiteRepository


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise AttendanceError("Выберите существующую базу данных.")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _validate(connection: sqlite3.Connection, mode: str) -> int:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in range(1, SCHEMA_VERSION + 1):
        raise AttendanceError("Неподдерживаемая версия базы данных.")
    validator = SQLiteRepository.__new__(SQLiteRepository)
    validator.mode = mode
    validator._validate_database(connection, version=version)
    return version


def _fingerprints(connection: sqlite3.Connection, columns=None):
    if columns is None:
        columns = {
            name: [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')]
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    result = {}
    for name, names in columns.items():
        fields = ",".join('"' + field.replace('"', '""') + '"' for field in names)
        table = name.replace('"', '""')
        rows = connection.execute(f'SELECT {fields} FROM "{table}"').fetchall()
        encoded = sorted(json.dumps(tuple(row), ensure_ascii=True) for row in rows)
        result[name] = {
            "count": len(rows),
            "sha256": hashlib.sha256("\n".join(encoded).encode()).hexdigest(),
        }
    return columns, result


def rehearse(source_path: Path, output: Path, *, mode: str = "real") -> dict:
    """Produce new private backup/candidate/plan; never overwrite an existing run."""
    output = output.expanduser().resolve()
    source_path = source_path.expanduser().resolve()
    if output.exists():
        raise AttendanceError("Для репетиции нужен новый каталог.")
    source = _open_readonly(source_path)
    try:
        version = _validate(source, mode)
        output.mkdir(parents=True, mode=0o700)
        os.chmod(output, 0o700)
        backup = output / "original.sqlite3"
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
    finally:
        source.close()
    os.chmod(backup, 0o600)
    candidate = output / "candidate.sqlite3"
    with closing(_open_readonly(backup)) as original:
        columns, before = _fingerprints(original)
        with closing(sqlite3.connect(candidate)) as destination:
            original.backup(destination)
    os.chmod(candidate, 0o600)
    repository = SQLiteRepository(candidate, mode=mode)
    try:
        _, after = _fingerprints(repository._connection, columns)
        if before != after:
            raise AttendanceError("Репетиция изменила исходную историю; применение запрещено.")
        plan = []
        for row in repository._connection.execute(
            "SELECT l.id,l.discipline_id,l.starts_at,l.ends_at,l.timezone,d.name,l.title, "
            "bl.block_id,sb.start_slot,tr.duration,bl.slot "
            "FROM lessons l JOIN disciplines d ON d.id=l.discipline_id "
            "LEFT JOIN block_lessons bl ON bl.lesson_id=l.id "
            "LEFT JOIN scheduled_blocks sb ON sb.id=bl.block_id "
            "LEFT JOIN teaching_rosters tr ON tr.id=sb.roster_id "
            "WHERE NOT EXISTS(SELECT 1 FROM course_lessons cl WHERE cl.block_id=bl.block_id) "
            "AND NOT EXISTS(SELECT 1 FROM deleted_lessons dl JOIN deletion_batches db ON db.id=dl.batch_id "
            "WHERE dl.lesson_id=l.id AND db.restored_at IS NULL) ORDER BY l.id"
        ):
            (
                lesson_id,
                discipline_id,
                start,
                end,
                timezone,
                name,
                title,
                block_id,
                block_slot,
                block_duration,
                pair_slot,
            ) = row
            local = datetime.fromisoformat(start).astimezone(ZoneInfo(timezone))
            slots = [
                i
                for i, pair in enumerate(SLOTS, 1)
                if pair[0] == local.strftime("%H:%M")
                and local.second == 0
                and local.microsecond == 0
            ]
            # Course/semester/group cannot safely be inferred from a label or date.
            java_lab = "java" in name.casefold() and any(
                word in name.casefold() for word in ("лаб", "lab")
            )
            plan.append(
                {
                    "lesson_id": lesson_id,
                    "legacy_discipline": name,
                    "discipline_id": discipline_id,
                    "old_starts_at": start,
                    "old_ends_at": end,
                    "timezone": timezone,
                    "old_local_start": local.isoformat(),
                    "old_local_end": datetime.fromisoformat(end)
                    .astimezone(ZoneInfo(timezone))
                    .isoformat(),
                    "local_day": local.date().isoformat(),
                    "start_slot": block_slot if block_id else (slots[0] if slots else None),
                    "existing_block_id": block_id,
                    "existing_pair_slot": pair_slot,
                    "course_key": None,
                    "topic_number": None,
                    "topic_title": title,
                    "merge_into": None,
                    "exact_start_slot": slots[0] if slots else None,
                    "required_duration": 2 if java_lab else block_duration,
                    "state": "needs_decision",
                    "decisions": ["semester_course", "topic", "roster", "combine_legacy_pairs"]
                    + ([] if slots else ["bell_slot"]),
                }
            )
        for block_id in {
            r["existing_block_id"] for r in plan if r["existing_block_id"] is not None
        }:
            block_rows = sorted(
                (r for r in plan if r["existing_block_id"] == block_id),
                key=lambda r: r["existing_pair_slot"],
            )
            for row in block_rows[1:]:
                row["merge_into"] = block_rows[0]["lesson_id"]
        report = {
            "source_version": version,
            "candidate_version": SCHEMA_VERSION,
            "integrity": "ok",
            "original_history_unchanged": True,
            "tables": before,
            "legacy_plan": plan,
            "application_allowed": not plan,
            "approved": False,
            "courses": [
                {
                    "key": f"course-{key}",
                    "discipline_id": key,
                    "legacy_discipline": name,
                    "name": name,
                    "academic_year_start": None,
                    "semester": None,
                    "study_course": None,
                    "kind": None,
                    "duration": None,
                }
                for key, name in sorted(
                    {(r["discipline_id"], r["legacy_discipline"]) for r in plan}
                )
            ],
        }
        (output / "plan.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(output / "plan.json", 0o600)
        from attendance_lab.infrastructure.migration_plan import write_approval_form

        write_approval_form(output, report)
        return report
    finally:
        repository.close()


def rollback(database: Path, backup: Path, *, mode: str = "real") -> None:
    """Restore exact old schema, without calling the auto-upgrading repository.

    Caller must hold the same application lock used for normal startup.
    """
    database = database.expanduser().resolve()
    backup = backup.expanduser().resolve()
    if not database.is_file() or database == backup or database.samefile(backup):
        raise AttendanceError("Выберите отдельную существующую резервную копию.")
    with closing(_open_readonly(backup)) as source:
        _validate(source, mode)
        with closing(_open_readonly(database)) as current:
            _validate(current, mode)
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
            safety = database.with_name(f"{database.stem}.before-rollback.{stamp}.sqlite3")
            with closing(sqlite3.connect(safety)) as destination:
                current.backup(destination)
            os.chmod(safety, 0o600)
        with closing(sqlite3.connect(database)) as destination:
            source.backup(destination)
            _validate(destination, mode)
