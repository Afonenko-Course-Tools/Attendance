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
