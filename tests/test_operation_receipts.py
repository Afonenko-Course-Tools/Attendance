"""Receipts share a durable commit with synthetic domain writes and survive restart."""

import hashlib
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from controller_test_support import registration_service
from redesign_test_support import logical_rows

from attendance_lab.application.operations import Operation, OperationExecutor
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SCHEMA_VERSION, SQLiteRepository


class LostReplyRepository(SQLiteRepository):
    fail_after_commit = False

    @contextmanager
    def operation_transaction(self):
        with super().operation_transaction():
            yield
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic transport lost the committed response")


def forbidden_action():
    raise AssertionError("a committed operation must not run its action again")


def test_lost_reply_reconciles_exact_result_after_restart(tmp_path):
    path = tmp_path / "synthetic-receipts.sqlite3"
    repository = LostReplyRepository(path, mode="demo")
    service = AttendanceService(repository)
    operation = Operation.create("create-1", "student.create", {"name": "Учебный Студент"})
    repository.fail_after_commit = True
    with pytest.raises(OSError, match="lost"):
        OperationExecutor(repository).execute(
            operation,
            lambda: {
                "id": service.create_student("Учебный Студент", student_id="stable-id").id,
                "sequence": [1, None, True],
            },
        )
    service.close()
    repository = SQLiteRepository(path, mode="demo")
    try:
        result = OperationExecutor(repository).execute(operation, forbidden_action)
        assert result == {"id": "stable-id", "sequence": [1, None, True]}
        assert [student.id for student in repository.list_students()] == ["stable-id"]
        assert repository.get_operation_receipt("create-1") is not None
    finally:
        repository.close()


def test_receipt_failure_rolls_back_domain_mutation_and_allows_explicit_retry(
    tmp_path, monkeypatch
):
    repository = SQLiteRepository(tmp_path / "synthetic-rollback.sqlite3", mode="demo")
    service = AttendanceService(repository)
    executor = OperationExecutor(repository)
    operation = Operation.create("create-1", "student.create", {"student_id": "stable-id"})

    def action():
        return {"student_id": service.create_student("Учебный Студент", student_id="stable-id").id}

    def fail_receipt(receipt):
        raise OSError("synthetic receipt failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(repository, "put_operation_receipt", fail_receipt)
            with pytest.raises(OSError):
                executor.execute(operation, action)
        assert service.list_students() == []
        assert repository.get_operation_receipt(operation.operation_id) is None
        assert executor.execute(operation, action) == {"student_id": "stable-id"}
    finally:
        service.close()


@pytest.mark.parametrize("changed", ["kind", "payload"])
def test_reused_id_with_changed_command_is_rejected_before_action(tmp_path, changed):
    repository = SQLiteRepository(tmp_path / "synthetic-mismatch.sqlite3", mode="demo")
    executor = OperationExecutor(repository)
    try:
        first = Operation.create("same-id", "test.write", {"ids": [1, 2], "reason": "one"})
        assert executor.execute(first, lambda: {"result": 7}) == {"result": 7}
        second = Operation.create(
            "same-id",
            "test.other" if changed == "kind" else "test.write",
            {"ids": [1, 2], "reason": "two" if changed == "payload" else "one"},
        )
        with pytest.raises(AttendanceError, match="другими параметрами"):
            executor.execute(second, forbidden_action)
    finally:
        repository.close()


def test_canonical_payload_and_result_are_json_safe_and_ambient_transaction_rejected(tmp_path):
    repository = SQLiteRepository(tmp_path / "synthetic-json.sqlite3", mode="demo")
    service = AttendanceService(repository)
    executor = OperationExecutor(repository)
    first = Operation.create("json-1", "write", {"b": 2, "a": 1})
    assert first == Operation.create("json-1", "write", {"a": 1, "b": 2})
    try:
        for payload in ({1: "lossy key"}, float("nan"), {"set": {1}}, (1, 2)):
            with pytest.raises(AttendanceError):
                Operation.create("invalid", "write", payload)
        with pytest.raises(AttendanceError, match="внешней транзакцией"):
            with repository.transaction():
                executor.execute(first, forbidden_action)

        def invalid_result():
            service.create_student("Учебный Студент", student_id="rolled-back")
            return {"invalid": object()}

        with pytest.raises(AttendanceError, match="JSON"):
            executor.execute(first, invalid_result)
        assert not service.list_students()
        assert repository.get_operation_receipt(first.operation_id) is None
    finally:
        service.close()


def test_manual_receipt_replay_after_revocation_does_not_restore_attendance(tmp_path):
    service, block = registration_service(tmp_path / "synthetic-manual.sqlite3")
    repository = service.repository
    executor = OperationExecutor(repository)
    student = service.list_students()[0]
    operation = Operation.create(
        "manual-1",
        "journal.manual",
        {"student_id": student.id, "pair_ids": list(block.pair_ids), "reason": "Учебное основание"},
    )

    def action():
        batch = service.redesign.create_manual_batch(
            {(student.id, pair_id) for pair_id in block.pair_ids}, "Учебное основание"
        )
        return {"batch_id": batch.id}

    try:
        first = executor.execute(operation, action)
        service.redesign.revoke_manual_batch(first["batch_id"])
        assert executor.execute(operation, forbidden_action) == first
        batches = service.redesign.list_manual_batches()
        assert len(batches) == 1 and batches[0].revoked_at is not None
        assert not any(e.active for e in service.redesign.pair_evidence(block.pair_ids[0]))
    finally:
        service.close()


def remove_post_v5_tables(connection):
    connection.execute("DROP TABLE course_score_variants")
    connection.execute("DROP TABLE manual_target_revocations")


def create_v4(path):
    repository = SQLiteRepository(path, mode="demo")
    service = AttendanceService(repository)
    service.create_student("Учебный Сохранённый", student_id="preserved-id")
    discipline = service.save_discipline("Учебная сохранённая дисциплина")
    year = service.redesign.save_year(2026)
    course = service.courses.save_course(discipline.id, year.id, 1, 2, "lab", 2, "Сохранённый курс")
    service.courses.save_topic(course.id, 7, "Сохранённая тема")
    service.grading.add_work(course.id, "Сохранённая работа", 20, 1.5)
    service.bind_card("AABB", "preserved-id")
    start = datetime(2026, 9, 1, 9, tzinfo=UTC)
    lesson = service.save_lesson(discipline.id, start, start + timedelta(hours=1), "UTC")
    service.set_lesson_roster(lesson.id, {"preserved-id"})
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, "AABB", start)
    service.close_lesson(lesson.id)
    service.redesign.create_manual_batch({("preserved-id", lesson.id)}, "Сохранённое основание")
    service.close()
    connection = sqlite3.connect(path)
    try:
        remove_post_v5_tables(connection)
        connection.execute("DROP TABLE operation_receipts")
        connection.execute("PRAGMA user_version=4")
        connection.commit()
    finally:
        connection.close()


def test_v4_upgrade_preserves_rows_and_saves_exact_v4_backup(tmp_path):
    path = tmp_path / "synthetic-v4.sqlite3"
    create_v4(path)
    with sqlite3.connect(path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
    before = logical_rows(path, tables)
    repository = SQLiteRepository(path, mode="demo")
    try:
        assert logical_rows(path, tuple(before)) == before
        assert repository.get_operation_receipt("absent") is None
    finally:
        repository.close()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    backups = list(tmp_path.glob("synthetic-v4.before-migration.*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert logical_rows(backups[0], tuple(before)) == before


def test_v4_failed_upgrade_rolls_back_receipt_schema_and_version(tmp_path):
    class FailingRepository(SQLiteRepository):
        def _validate_database(self, connection, *, version=SCHEMA_VERSION):
            super()._validate_database(connection, version=version)
            if version == SCHEMA_VERSION:
                raise AttendanceError("synthetic final migration failure")

    path = tmp_path / "synthetic-v4.sqlite3"
    create_v4(path)
    before = logical_rows(path, ("students", "cards", "lessons"))
    with pytest.raises(AttendanceError, match="synthetic"):
        FailingRepository(path, mode="demo")
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE name IN ('operation_receipts', 'manual_target_revocations', 'course_score_variants')"
        ).fetchall()
    assert logical_rows(path, tuple(before)) == before


def test_restore_v4_migrates_private_copy_and_v5_backup_preserves_receipts(tmp_path):
    old = tmp_path / "synthetic-v4.sqlite3"
    create_v4(old)
    source_digest = hashlib.sha256(old.read_bytes()).digest()
    repository = SQLiteRepository(tmp_path / "synthetic-current.sqlite3", mode="demo")
    operation = Operation.create("receipt-preserved", "test", {"value": 1})
    executor = OperationExecutor(repository)
    try:
        assert executor.execute(operation, lambda: {"original": True}) == {"original": True}
        snapshot = tmp_path / "synthetic-v5-backup.sqlite3"
        repository.backup(snapshot)
        with closing(sqlite3.connect(snapshot)) as connection, connection:
            remove_post_v5_tables(connection)
            connection.execute("PRAGMA user_version=5")
        repository.restore(old)
        assert [s.id for s in repository.list_students()] == ["preserved-id"]
        assert hashlib.sha256(old.read_bytes()).digest() == source_digest
        repository.restore(snapshot)
        assert executor.execute(operation, forbidden_action) == {"original": True}
    finally:
        repository.close()


def test_v5_validation_rejects_missing_receipt_safety_and_receipts_are_immutable(tmp_path):
    path = tmp_path / "synthetic-validation.sqlite3"
    repository = SQLiteRepository(path, mode="demo")
    executor = OperationExecutor(repository)
    executor.execute(Operation.create("immutable", "test", None), lambda: {"saved": True})
    repository.close()
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE operation_receipts SET kind='changed'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM operation_receipts")
        remove_post_v5_tables(connection)
        connection.execute("PRAGMA user_version=5")
        connection.execute("DROP TRIGGER operation_receipts_no_update")
    with pytest.raises(AttendanceError, match="Структура"):
        SQLiteRepository(path, mode="demo")
