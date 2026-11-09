"""Synthetic evidence that rehearsal and rollback preserve original history."""

import json
import sqlite3

import pytest
from redesign_test_support import create_v1_database, logical_rows

from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.migration import rehearse, rollback
from attendance_lab.infrastructure.sqlite import SCHEMA_VERSION, SQLiteRepository


def test_all_upgrade_steps_roll_back_together(tmp_path):
    class FailingMigration(SQLiteRepository):
        def _validate_database(self, connection, *, version=SCHEMA_VERSION):
            super()._validate_database(connection, version=version)
            if version == SCHEMA_VERSION:
                raise AttendanceError("Synthetic final validation failure")

    database = tmp_path / "old.sqlite3"
    create_v1_database(database)
    before = logical_rows(database, ("students", "cards", "scans", "binding_changes"))
    with pytest.raises(AttendanceError):
        FailingMigration(database)
    assert logical_rows(database, tuple(before)) == before
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name='academic_years'"
        ).fetchall()


def test_rehearsal_includes_live_wal_without_modifying_source(tmp_path):
    database = tmp_path / "live.sqlite3"
    create_v1_database(database)
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO cards VALUES ('00001122',NULL)")
        writer.commit()
        before = logical_rows(database, ("students", "cards", "scans", "binding_changes"))
        report = rehearse(database, tmp_path / "private")
        assert report["original_history_unchanged"]
        assert report["source_version"] == 1
        assert report["candidate_version"] == SCHEMA_VERSION
        assert len(report["legacy_plan"]) == 2
        assert not report["application_allowed"]
        assert logical_rows(database, tuple(before)) == before
        assert logical_rows(tmp_path / "private/original.sqlite3", tuple(before)) == before
        assert logical_rows(
            tmp_path / "private/candidate.sqlite3", ("cards", "scans", "binding_changes")
        ) == {k: before[k] for k in ("cards", "scans", "binding_changes")}
        assert writer.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (tmp_path / "private").stat().st_mode & 0o777 == 0o700
        # Manifest contains IDs and schedule decisions, never card/student rows.
        plan = json.loads((tmp_path / "private/plan.json").read_text())
        assert plan["legacy_plan"][0]["exact_start_slot"] == 1
        assert plan["legacy_plan"][0]["state"] == "needs_decision"
        with pytest.raises(AttendanceError):
            rehearse(database, tmp_path / "private")


def test_rollback_restores_exact_original_version(tmp_path):
    database = tmp_path / "working.sqlite3"
    create_v1_database(database)
    before = logical_rows(database, ("students", "cards", "scans", "binding_changes"))
    rehearse(database, tmp_path / "private")
    repository = SQLiteRepository(database)
    repository.close()
    rollback(database, tmp_path / "private/original.sqlite3")
    assert logical_rows(database, tuple(before)) == before
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert list(tmp_path.glob("*.before-rollback.*.sqlite3"))


def test_java_labs_require_two_pairs_without_synthetic_scans(tmp_path):
    database = tmp_path / "java.sqlite3"
    create_v1_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE disciplines SET name='Java лабораторные'")
    report = rehearse(database, tmp_path / "private")
    assert all(row["required_duration"] == 2 for row in report["legacy_plan"])
    assert report["tables"]["scans"]["count"] == 1
    with sqlite3.connect(tmp_path / "private/candidate.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM course_lessons").fetchone()[0] == 0


def _approve(report, path, *, duration=2, merge=False):
    import copy

    approved = copy.deepcopy(report)
    approved["approved"] = True
    for course in approved["courses"]:
        course.update(
            academic_year_start=2026, semester=1, study_course=2, kind="lab", duration=duration
        )
    for index, row in enumerate(approved["legacy_plan"]):
        row.update(
            course_key=approved["courses"][0]["key"],
            topic_number=1 if merge else index + 1,
            topic_title="Тема" if merge else f"Тема {index + 1}",
            start_slot=1,
        )
    if merge:
        approved["legacy_plan"][1]["merge_into"] = approved["legacy_plan"][0]["lesson_id"]
    path.write_text(json.dumps(approved, ensure_ascii=False))
    return path


@pytest.mark.parametrize("merge", [False, True])
def test_apply_explicit_approved_plan_only_to_new_copy(tmp_path, merge):
    from attendance_lab.infrastructure.migration_plan import apply_approved

    database = tmp_path / "old.sqlite3"
    create_v1_database(database)
    before = logical_rows(
        database, ("students", "cards", "scans", "binding_changes", "lesson_roster")
    )
    report = rehearse(database, tmp_path / "review")
    approved = _approve(report, tmp_path / "approved.json", merge=merge)
    result = apply_approved(database, approved, tmp_path / "applied")
    assert result["blocks"] == (1 if merge else 2)
    assert logical_rows(database, tuple(before)) == before
    with sqlite3.connect(tmp_path / "applied/candidate.sqlite3") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM lessons").fetchone()[0] == (
            2 if merge else 4
        )
        assert connection.execute("SELECT count(*) FROM block_lessons").fetchone()[0] == (
            2 if merge else 4
        )
        assert connection.execute("SELECT count(*) FROM roster_students").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM metadata WHERE key=?", ("approved_legacy_migration",)
            ).fetchone()[0]
            == 1
        )
    assert logical_rows(
        tmp_path / "applied/candidate.sqlite3", ("cards", "scans", "binding_changes")
    ) == {k: before[k] for k in ("cards", "scans", "binding_changes")}
    assert (tmp_path / "review/review-plan.html").is_file()


def test_apply_rejects_incomplete_or_changed_snapshot(tmp_path):
    from attendance_lab.infrastructure.migration_plan import apply_approved

    database = tmp_path / "old.sqlite3"
    create_v1_database(database)
    report = rehearse(database, tmp_path / "review")
    approved = _approve(report, tmp_path / "approved.json")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO cards VALUES ('00003344',NULL)")
    with pytest.raises(AttendanceError):
        apply_approved(database, approved, tmp_path / "rejected")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_install_approved_copy_checks_live_revision_and_can_rollback(tmp_path):
    from attendance_lab.infrastructure.migration_plan import apply_approved, install_approved_copy

    database = tmp_path / "working.sqlite3"
    create_v1_database(database)
    report = rehearse(database, tmp_path / "review")
    approved = _approve(report, tmp_path / "approved.json")
    apply_approved(database, approved, tmp_path / "applied")
    install_approved_copy(database, tmp_path / "applied/candidate.sqlite3")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT count(*) FROM course_lessons").fetchone()[0] == 2
    rollback(database, tmp_path / "review/original.sqlite3")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO cards VALUES ('00005566',NULL)")
    with pytest.raises(AttendanceError):
        install_approved_copy(database, tmp_path / "applied/candidate.sqlite3")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 2


def test_existing_v3_block_keeps_session_evidence_ids_and_unclassified_students(tmp_path):
    from redesign_test_support import LEGACY_FIRST, LEGACY_LAST, LEGACY_STUDENT, LEGACY_UID

    from attendance_lab.application.service import AttendanceService
    from attendance_lab.infrastructure.migration_plan import apply_approved
    from attendance_lab.infrastructure.redesign_schema import SCHEMA_V2
    from attendance_lab.infrastructure.sqlite import _SCHEMA_V3

    database = tmp_path / "old-block.sqlite3"
    create_v1_database(database)
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA_V2 + _SCHEMA_V3)
        connection.execute("PRAGMA user_version=3")
        connection.execute("INSERT INTO academic_years VALUES(8,2026)")
        connection.execute(
            "INSERT INTO teaching_rosters VALUES(12,17,8,1,'Старый состав','lab',2,'students')"
        )
        connection.execute("INSERT INTO roster_students VALUES(12,?)", (LEGACY_STUDENT,))
        connection.execute(
            "INSERT INTO scheduled_blocks VALUES(33,12,'2026-09-07',1,'Europe/Minsk','','')"
        )
        connection.executemany("INSERT INTO block_lessons VALUES(33,?,?)", [(41, 1), (42, 2)])
        connection.execute(
            "INSERT INTO registration_sessions VALUES(77,33,?,?,'closed')",
            (LEGACY_FIRST, LEGACY_LAST),
        )
        connection.executemany("INSERT INTO session_target_pairs VALUES(77,?)", [(41,), (42,)])
        connection.execute(
            "INSERT INTO card_evidence VALUES(91,77,?,?)", (LEGACY_UID, LEGACY_FIRST)
        )
    before = logical_rows(
        database, ("scans", "registration_sessions", "session_target_pairs", "card_evidence")
    )
    report = rehearse(database, tmp_path / "review")
    assert len(report["legacy_plan"]) == 2
    assert all(r["existing_block_id"] == 33 for r in report["legacy_plan"])
    approved = _approve(report, tmp_path / "approved.json", merge=True)
    apply_approved(database, approved, tmp_path / "applied")
    candidate = tmp_path / "applied/candidate.sqlite3"
    assert logical_rows(candidate, tuple(before)) == before
    with sqlite3.connect(candidate) as connection:
        assert connection.execute("SELECT id FROM scheduled_blocks").fetchall() == [(33,)]
        assert connection.execute(
            "SELECT lesson_id FROM block_lessons ORDER BY slot"
        ).fetchall() == [(41,), (42,)]
    service = AttendanceService(SQLiteRepository(candidate))
    try:
        course = service.courses.list_courses()[0]
        assert [s.student_id for s in service.courses.list_students(course.id)] == [LEGACY_STUDENT]
    finally:
        service.close()


def test_plan_can_hide_test_row_without_academic_choices_and_restore_it(tmp_path):
    from datetime import UTC, datetime, timedelta

    from attendance_lab.infrastructure.migration_plan import apply_approved

    database = tmp_path / "source.sqlite3"
    create_v1_database(database)
    before = logical_rows(database, ("scans", "cards", "binding_changes", "lessons"))
    report = rehearse(database, tmp_path / "review")
    approved = report.copy()
    approved["approved"] = True
    approved["legacy_plan"] = [
        dict(
            r, action="delete", course_key=None, topic_number=None, topic_title="", start_slot=None
        )
        for r in report["legacy_plan"]
    ]
    path = tmp_path / "approved.json"
    path.write_text(json.dumps(approved))
    result = apply_approved(database, path, tmp_path / "applied")
    assert result["blocks"] == 0 and result["deleted_lessons"] == 2
    assert logical_rows(database, tuple(before)) == before
    candidate = tmp_path / "applied/candidate.sqlite3"
    assert logical_rows(candidate, tuple(before)) == before
    repository = SQLiteRepository(candidate)
    try:
        assert repository.ll_deleted_lesson_ids() == {41, 42}
        batch = repository.ll_deletions()[0]
        assert batch.lesson_ids == (41, 42) and batch.restored_at is None
        with repository.transaction():
            repository.ll_restore(batch.id, datetime.now(UTC) + timedelta(seconds=1))
        assert repository.ll_deleted_lesson_ids() == set()
        assert repository.ll_deletions()[0].restored_at is not None
        with pytest.raises(AttendanceError):
            with repository.transaction():
                repository.ll_restore(batch.id, datetime.now(UTC) + timedelta(seconds=2))
    finally:
        repository.close()


def test_lifecycle_failed_multirow_delete_is_atomic(tmp_path):
    from datetime import UTC, datetime

    database = tmp_path / "source.sqlite3"
    create_v1_database(database)
    repository = SQLiteRepository(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with repository.transaction():
                repository.ll_delete({41, 999}, datetime.now(UTC), "Синтетический тест")
        assert repository.ll_deletions() == []
        assert repository.ll_deleted_lesson_ids() == set()
    finally:
        repository.close()


def _addition(source=41, day="2026-09-14", number=3, title="Пропущенная тема"):
    return {
        "key": "synthetic-addition",
        "course_key": "course-17",
        "roster_source_lesson_id": source,
        "local_day": day,
        "date_confirmed": True,
        "start_slot": 1,
        "topic_number": number,
        "topic_title": title,
    }


@pytest.mark.parametrize("count", [1, 2])
def test_additional_historical_lesson_copies_only_roster_without_attendance(tmp_path, count):
    from redesign_test_support import LEGACY_STUDENT

    from attendance_lab.infrastructure.migration_plan import apply_approved

    database = tmp_path / "source.sqlite3"
    create_v1_database(database)
    before = logical_rows(database, ("scans", "cards", "binding_changes"))
    report = rehearse(database, tmp_path / "review")
    path = _approve(report, tmp_path / "approved.json", duration=1)
    approved = json.loads(path.read_text())
    approved["additional_lessons"] = [_addition()]
    if count == 2:
        approved["additional_lessons"].append(
            dict(
                _addition(day="2026-09-21", number=4, title="Ещё одна тема"), key="second-addition"
            )
        )
    path.write_text(json.dumps(approved))
    result = apply_approved(database, path, tmp_path / "applied")
    assert result["additional_lessons"] == count and result["blocks"] == 2 + count
    assert logical_rows(database, tuple(before)) == before
    candidate = tmp_path / "applied/candidate.sqlite3"
    assert logical_rows(candidate, tuple(before)) == before
    with sqlite3.connect(candidate) as connection:
        pair = connection.execute(
            "SELECT id,state,roster_fixed FROM lessons WHERE id NOT IN (41,42)"
        ).fetchone()
        assert pair is not None and pair[1:] == ("completed", 1)
        assert connection.execute(
            "SELECT student_id FROM lesson_roster WHERE lesson_id=?", (pair[0],)
        ).fetchall() == [(LEGACY_STUDENT,)]
        assert (
            connection.execute(
                "SELECT count(*) FROM scans WHERE lesson_id=?", (pair[0],)
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT count(*) FROM card_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM manual_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM registration_sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM course_lessons").fetchone()[0] == 2 + count
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    "change",
    [
        {"date_confirmed": False},
        {"local_day": "invalid"},
        {"local_day": "2026-09-07"},
        {"roster_source_lesson_id": 999},
        {"course_key": "unknown"},
        {"topic_number": 1, "topic_title": "Другая тема с тем же номером"},
        {"start_slot": 9},
    ],
)
def test_additional_lesson_rejects_unconfirmed_date_collision_and_bad_context(tmp_path, change):
    from attendance_lab.infrastructure.migration_plan import apply_approved

    database = tmp_path / "source.sqlite3"
    create_v1_database(database)
    report = rehearse(database, tmp_path / "review")
    path = _approve(report, tmp_path / "approved.json", duration=1)
    approved = json.loads(path.read_text())
    approved["additional_lessons"] = [dict(_addition(), **change)]
    path.write_text(json.dumps(approved))
    with pytest.raises(AttendanceError):
        apply_approved(database, path, tmp_path / "rejected")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM lessons").fetchone()[0] == 2
