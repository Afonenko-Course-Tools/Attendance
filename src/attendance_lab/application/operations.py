"""Durable command identity, independent of a transport or a particular database."""

import hashlib
import json
import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from attendance_lab.domain.models import AttendanceError

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]


def canonical_json(value: object) -> str:
    """Reject lossy coercions and non-JSON values before any mutation happens."""

    def validate(item):
        if item is None or type(item) in (bool, int, str):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        raise AttendanceError("Операция должна содержать только конечные JSON-значения.")

    validate(value)
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: str
    kind: str
    payload_json: str

    def __post_init__(self):
        if not self.operation_id.strip() or not self.kind.strip():
            raise AttendanceError("Идентификатор и вид операции обязательны.")
        try:
            payload = json.loads(self.payload_json)
        except (ValueError, TypeError) as exc:
            raise AttendanceError("Некорректные параметры операции.") from exc
        if canonical_json(payload) != self.payload_json:
            raise AttendanceError("Параметры операции должны иметь каноническую JSON-форму.")

    @classmethod
    def create(cls, operation_id: str, kind: str, payload: object) -> Operation:
        return cls(operation_id, kind, canonical_json(payload))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    operation_id: str
    kind: str
    fingerprint: str
    result_json: str


class OperationRepository(Protocol):
    def operation_transaction(self) -> AbstractContextManager[None]: ...
    def get_operation_receipt(self, operation_id: str) -> OperationReceipt | None: ...
    def put_operation_receipt(self, receipt: OperationReceipt) -> None: ...


class OperationExecutor:
    def __init__(self, repository: OperationRepository):
        self.repository = repository

    def execute(self, operation: Operation, action: Callable[[], object]) -> JSONValue:
        with self.repository.operation_transaction():
            receipt = self.repository.get_operation_receipt(operation.operation_id)
            if receipt is not None:
                if (receipt.kind, receipt.fingerprint) != (operation.kind, operation.fingerprint):
                    raise AttendanceError(
                        "Идентификатор операции уже использован с другими параметрами."
                    )
                result = json.loads(receipt.result_json)
            else:
                # Validation failure rolls the action back together with its receipt.
                result_json = canonical_json(action())
                self.repository.put_operation_receipt(
                    OperationReceipt(
                        operation.operation_id, operation.kind, operation.fingerprint, result_json
                    )
                )
                result = json.loads(result_json)
        # Never acknowledge until the top-level transaction has committed.
        return result
