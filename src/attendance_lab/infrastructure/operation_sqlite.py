"""SQLite receipt adapter; receipt and domain data share the same connection/UoW."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from attendance_lab.application.operations import OperationReceipt
from attendance_lab.domain.models import AttendanceError

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


OPERATION_SCHEMA = """
CREATE TABLE operation_receipts (
    operation_id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(operation_id)) > 0),
    kind TEXT NOT NULL CHECK(length(trim(kind)) > 0),
    fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64
        AND fingerprint NOT GLOB '*[^0-9a-f]*'),
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    committed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TRIGGER operation_receipts_no_update BEFORE UPDATE ON operation_receipts
BEGIN SELECT RAISE(ABORT, 'operation receipts are immutable'); END;
CREATE TRIGGER operation_receipts_no_delete BEFORE DELETE ON operation_receipts
BEGIN SELECT RAISE(ABORT, 'operation receipts are immutable'); END;
"""
OPERATION_TABLE_COLUMNS = {
    "operation_receipts": {"operation_id", "kind", "fingerprint", "result_json", "committed_at"}
}


class OperationSQLiteMixin:
    if TYPE_CHECKING:
        _connection: sqlite3.Connection
        _depth: int

        def transaction(self, *, write: bool = True) -> AbstractContextManager[None]: ...

    @contextmanager
    def operation_transaction(self) -> Iterator[None]:
        if self._depth:
            raise AttendanceError("Надёжная операция должна владеть внешней транзакцией.")
        with self.transaction():
            yield

    def get_operation_receipt(self, operation_id: str) -> OperationReceipt | None:
        row = self._connection.execute(
            "SELECT operation_id,kind,fingerprint,result_json FROM operation_receipts "
            "WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        return OperationReceipt(*row) if row else None

    def put_operation_receipt(self, receipt: OperationReceipt) -> None:
        if not self._depth:
            raise AttendanceError("Подтверждение операции требует транзакции.")
        self._connection.execute(
            "INSERT INTO operation_receipts(operation_id,kind,fingerprint,result_json) VALUES(?,?,?,?)",
            (receipt.operation_id, receipt.kind, receipt.fingerprint, receipt.result_json),
        )
