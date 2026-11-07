"""Restore rejects invalid sources before modifying an existing synthetic database."""

import sqlite3

import pytest

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.mark.parametrize("malformed", ["empty", "missing_table"])
def test_invalid_restore_does_not_erase_current_directory(tmp_path, malformed):
    source = tmp_path / "invalid.sqlite3"
    if malformed == "empty":
        source.touch()
    else:
        source_repository = SQLiteRepository(source)
        source_repository.close()
        with sqlite3.connect(source) as connection:
            connection.execute("DROP TABLE import_rows")
    service = AttendanceService(SQLiteRepository(tmp_path / "current.sqlite3"))
    try:
        service.create_student("Тест Сохранённый", student_id="preserve")
        with pytest.raises(AttendanceError):
            service.restore(source)
        assert [s.id for s in service.list_students()] == ["preserve"]
        assert not list(tmp_path.glob("current.before-restore.*"))
    finally:
        service.close()


def create_v2(path):
    from redesign_test_support import create_v1_database

    from attendance_lab.infrastructure.redesign_sqlite import SCHEMA_V2

    create_v1_database(path)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_V2)
        connection.execute("PRAGMA user_version = 2")


def test_v2_upgrade_preserves_audit_ids_and_permits_audited_unbinding(tmp_path):
    from redesign_test_support import LEGACY_STUDENT, LEGACY_UID, logical_rows

    path = tmp_path / "synthetic-v2.sqlite3"
    create_v2(path)
    before = logical_rows(path, ("binding_changes", "scans", "cards"))
    service = AttendanceService(SQLiteRepository(path))
    try:
        assert logical_rows(path, ("binding_changes", "scans", "cards")) == before
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        backups = list(tmp_path.glob("synthetic-v2.before-migration.*.sqlite3"))
        assert len(backups) == 1
        assert logical_rows(backups[0], ("binding_changes", "scans", "cards")) == before
        with sqlite3.connect(backups[0]) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        service.unbind_card(LEGACY_UID, expected_student_id=LEGACY_STUDENT)
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT id FROM binding_changes ORDER BY id").fetchall() == [
                (9,),
                (10,),
            ]
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE binding_changes SET correction=2 WHERE id=10")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE binding_changes SET scope='future' WHERE id=10")
    finally:
        service.close()


def test_restore_v2_migrates_private_copy_only(tmp_path):
    from redesign_test_support import LEGACY_STUDENT, LEGACY_UID

    source = tmp_path / "synthetic-v2.sqlite3"
    create_v2(source)
    original = source.read_bytes()
    service = AttendanceService(SQLiteRepository(tmp_path / "current.sqlite3"))
    try:
        service.restore(source)
        assert source.read_bytes() == original
        service.unbind_card(LEGACY_UID, expected_student_id=LEGACY_STUDENT)
        assert service.repository.get_card(LEGACY_UID).student_id is None
    finally:
        service.close()


def test_v2_migration_failure_rolls_back_table_and_version(tmp_path):
    from redesign_test_support import logical_rows

    class FailingMigration(SQLiteRepository):
        def _validate_database(self, connection, *, version=3):
            super()._validate_database(connection, version=version)
            if version == 3:
                raise AttendanceError("synthetic validation failure")

    path = tmp_path / "synthetic-v2.sqlite3"
    create_v2(path)
    before = logical_rows(path, ("binding_changes", "cards", "scans"))
    with pytest.raises(AttendanceError):
        FailingMigration(path)
    assert logical_rows(path, ("binding_changes", "cards", "scans")) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = {
            row[1]: row[3] for row in connection.execute("PRAGMA table_info(binding_changes)")
        }
        assert columns["student_id"] == 1
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name='binding_changes_v3'"
        ).fetchall()


def test_restore_rejects_v3_marker_with_nonnullable_audit_owner(tmp_path):
    source = tmp_path / "synthetic-invalid-v3.sqlite3"
    create_v2(source)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA user_version = 3")
    service = AttendanceService(SQLiteRepository(tmp_path / "current.sqlite3"))
    try:
        service.create_student("Сохранить", student_id="keep")
        with pytest.raises(AttendanceError):
            service.restore(source)
        assert [s.id for s in service.list_students()] == ["keep"]
        assert not list(tmp_path.glob("current.before-restore.*"))
    finally:
        service.close()
