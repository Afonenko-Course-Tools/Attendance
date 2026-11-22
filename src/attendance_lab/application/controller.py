"""FIFO card admission and durable, retryable registration commands without Qt."""

import json
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol, cast
from uuid import uuid4

from attendance_lab.application.operations import JSONValue, Operation, OperationExecutor
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError, PublicResult
from attendance_lab.domain.rules import normalize_uid

logger = logging.getLogger(__name__)


class Reader(Protocol):
    def list_readers(self) -> list[str]: ...
    def start(self, name: str) -> None: ...
    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CapturedScan:
    lesson_id: int
    uid: str
    at: datetime
    session_id: int | None = None
    operation_id: str = field(default_factory=lambda: str(uuid4()))
    block_id: int | None = None
    target_pair_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _AdmissionContext:
    lesson_id: int
    session_id: int | None
    block_id: int | None
    target_pair_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ControllerEvent:
    kind: Literal["public", "status", "changed", "failed"]
    result: PublicResult | None = None
    text: str = ""
    scan: CapturedScan | None = None
    lesson_id: int | None = None
    session_id: int | None = None


class PendingScansError(AttendanceError):
    """The FIFO head is retained; only an explicit retry may resume it."""


class AttendanceController:
    """Reader callbacks capture immutable context; callers own all service execution.

    Failed heads block subsequent writes and transitions. Closing bars admissions
    before draining. The callback lock is never held while joining a reader.
    """

    def __init__(
        self,
        service: AttendanceService,
        reader_factory: Callable[[Callable[[str, datetime], None], Callable[[str], None]], Reader],
        *,
        operations: OperationExecutor | None = None,
    ) -> None:
        self.service = service
        self.operations = operations or OperationExecutor(service.repository)
        self._lock = RLock()
        self._pending: deque[CapturedScan] = deque()
        self._events: deque[ControllerEvent] = deque()
        self.active_lesson_id: int | None = None
        self.active_session_id: int | None = None
        self._active_block_id: int | None = None
        self._active_pair_ids: tuple[int, ...] = ()
        self._accepting = False
        self._admission: _AdmissionContext | None = None
        self._blocked = False
        self._stopped = False
        # Keeps context stable for same-process callers using only operation_id.
        # Transport retries after restart must provide the explicit target ID.
        self._transitions: dict[str, Operation] = {}
        self.reader = reader_factory(self._capture, self._status)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def blocked(self) -> bool:
        with self._lock:
            return self._blocked

    @property
    def accepting(self) -> bool:
        with self._lock:
            return self._accepting and not self._stopped

    def _capture(self, uid: str, at: datetime) -> None:
        # One atomic reference read before waiting for a transition's lock. A
        # callback already in flight must not acquire the next session's context.
        admission = self._admission
        with self._lock:
            if self._stopped:
                return
            if admission is None or admission is not self._admission or not self._accepting:
                self._events.append(ControllerEvent("status", text="Регистрация закрыта"))
                return
            self._pending.append(
                CapturedScan(
                    admission.lesson_id,
                    uid,
                    at,
                    admission.session_id,
                    block_id=admission.block_id,
                    target_pair_ids=admission.target_pair_ids,
                )
            )

    def _status(self, text: str) -> None:
        with self._lock:
            if not self._stopped:
                self._events.append(ControllerEvent("status", text=text))

    def available_readers(self) -> list[str]:
        try:
            return self.reader.list_readers()
        except Exception as exc:
            self._status(f"Не удалось получить список считывателей: {exc}")
            return []

    def connect_reader(self, name: str) -> None:
        self.reader.stop()
        self.reader.start(name)

    def _record(self, scan: CapturedScan) -> PublicResult:
        normalized = replace(scan, uid=normalize_uid(scan.uid))
        operation = Operation.create(
            scan.operation_id,
            "registration.scan",
            {
                "lesson_id": scan.lesson_id,
                "session_id": scan.session_id,
                "block_id": scan.block_id,
                "target_pair_ids": list(scan.target_pair_ids),
                "uid": normalized.uid,
                "at": scan.at.isoformat(),
            },
        )

        def action() -> JSONValue:
            if scan.session_id is not None:
                session = self.service.redesign.get_session(scan.session_id)
                if (scan.block_id is not None and session.block_id != scan.block_id) or (
                    scan.target_pair_ids and session.target_pair_ids != scan.target_pair_ids
                ):
                    raise AttendanceError("Контекст принятого предъявления изменился.")
            result = (
                self.service.redesign.record_session_scan(scan.session_id, normalized.uid, scan.at)
                if scan.session_id is not None
                else self.service.record_scan(scan.lesson_id, normalized.uid, scan.at)
            )
            return {
                "kind": result.kind,
                "message": result.message,
                "full_name": result.full_name,
                "at": result.at.isoformat() if result.at else None,
            }

        result = cast(dict[str, JSONValue], self.operations.execute(operation, action))
        at = result["at"]
        return PublicResult(
            cast(Literal["idle", "success", "repeat", "error", "closed"], result["kind"]),
            cast(str, result["message"]),
            cast(str | None, result["full_name"]),
            datetime.fromisoformat(cast(str, at)) if at is not None else None,
        )

    def _flush(self) -> bool:
        if self._blocked:
            return False
        while self._pending:
            scan = self._pending[0]
            try:
                result = self._record(scan)
            except Exception as exc:
                logger.exception("Card registration failed for lesson %s", scan.lesson_id)
                self._blocked = True
                result = PublicResult(
                    "error", "Не удалось сохранить отметку. Требуется явный повтор."
                )
                self._events.extend(
                    [
                        ControllerEvent(
                            "status",
                            text=f"Ошибка регистрации: {exc}. Очередь приостановлена; повторите сохранение.",
                        ),
                        ControllerEvent(
                            "failed",
                            result=result,
                            scan=scan,
                            lesson_id=scan.lesson_id,
                            session_id=scan.session_id,
                        ),
                        ControllerEvent("public", result=result),
                    ]
                )
                return False
            # Only a confirmed durable receipt permits removal and public success.
            self._pending.popleft()
            self._events.append(
                ControllerEvent(
                    "changed", result=result, scan=replace(scan, uid=normalize_uid(scan.uid))
                )
            )
            self._events.append(ControllerEvent("public", result=result))
        return True

    def flush_pending(self) -> None:
        with self._lock:
            if not self._flush():
                raise PendingScansError("Есть несохранённые предъявления. Выполните явный повтор.")

    def retry_pending(self) -> bool:
        with self._lock:
            self._blocked = False
            return self._flush()

    def _operation(
        self,
        kind: str,
        payload: object,
        operation_id: str | None,
        *,
        reuse_context: bool = False,
    ) -> Operation:
        identity = operation_id or str(uuid4())
        candidate = Operation.create(identity, kind, payload)
        previous = self._transitions.get(identity)
        if reuse_context and previous is not None and previous.kind == kind:
            return previous
        self._transitions[identity] = candidate
        return candidate

    def _clear_context(self) -> None:
        self.active_lesson_id = self.active_session_id = self._active_block_id = None
        self._active_pair_ids = ()
        self._accepting = False
        self._admission = None

    def _refresh_context(self) -> None:
        """Project storage after replay; a historical receipt never reopens its session."""
        sessions = [s for s in self.service.redesign.store.rd_sessions() if s.closed_at is None]
        self._clear_context()
        if sessions:
            session = sessions[0]
            self.active_session_id = session.id
            self.active_lesson_id = min(session.target_pair_ids)
            self._active_block_id = session.block_id
            self._active_pair_ids = session.target_pair_ids
            self._accepting = True
        else:
            lesson = next(
                (
                    candidate
                    for candidate in self.service.list_lessons()
                    if candidate.state == "open"
                ),
                None,
            )
            if lesson is not None:
                self.active_lesson_id = lesson.id
                self._active_pair_ids = (lesson.id,)
                self._accepting = True
        if self._accepting and self.active_lesson_id is not None:
            self._admission = _AdmissionContext(
                self.active_lesson_id,
                self.active_session_id,
                self._active_block_id,
                self._active_pair_ids,
            )

    def _changed(self) -> None:
        self._events.extend(
            [
                ControllerEvent("changed"),
                ControllerEvent(
                    "public",
                    PublicResult(
                        "idle" if self._accepting else "closed",
                        "Приложите карту" if self._accepting else "Регистрация закрыта",
                    ),
                ),
            ]
        )

    def open_lesson(self, lesson_id: int, *, operation_id: str | None = None) -> None:
        with self._lock:
            self._accepting = False
            self._admission = None
            self.flush_pending()
            operation = self._operation(
                "registration.open_lesson", {"lesson_id": lesson_id}, operation_id
            )
            self.operations.execute(operation, lambda: self._open_legacy(lesson_id))
            self._refresh_context()
            self._changed()

    def _open_legacy(self, lesson_id: int) -> JSONValue:
        self.service.open_lesson(lesson_id)
        return {"lesson_id": lesson_id}

    def open_session(
        self,
        block_id: int,
        target_pair_ids: set[int],
        *,
        operation_id: str | None = None,
    ) -> int:
        with self._lock:
            self._accepting = False
            self._admission = None
            self.flush_pending()
            operation = self._operation(
                "registration.open_session",
                {"block_id": block_id, "target_pair_ids": sorted(target_pair_ids)},
                operation_id,
            )

            def action() -> JSONValue:
                session = self.service.redesign.open_session(block_id, target_pair_ids)
                return {"session_id": session.id}

            response = cast(dict[str, JSONValue], self.operations.execute(operation, action))
            session_id = cast(int, response["session_id"])
            self._refresh_context()
            self._changed()
            return session_id

    def close_lesson(
        self,
        *,
        operation_id: str | None = None,
        lesson_id: int | None = None,
        session_id: int | None = None,
    ) -> None:
        with self._lock:
            self._accepting = False  # Admission barrier precedes the first write/flush.
            self._admission = None
            self.flush_pending()
            target_session = session_id if session_id is not None else self.active_session_id
            target_lesson = lesson_id if lesson_id is not None else self.active_lesson_id
            if target_session is not None:
                target_lesson = None
            operation = self._operation(
                "registration.close",
                {"session_id": target_session, "lesson_id": target_lesson},
                operation_id,
                reuse_context=lesson_id is None and session_id is None,
            )
            payload = cast(dict[str, JSONValue], json.loads(operation.payload_json))

            def action() -> JSONValue:
                if payload["session_id"] is not None:
                    self.service.redesign.close_session(cast(int, payload["session_id"]))
                elif payload["lesson_id"] is not None:
                    self.service.close_lesson(cast(int, payload["lesson_id"]))
                return None

            self.operations.execute(operation, action)
            self._refresh_context()
            self._changed()

    def close_session(
        self,
        *,
        operation_id: str | None = None,
        session_id: int | None = None,
    ) -> None:
        self.close_lesson(operation_id=operation_id, session_id=session_id)

    def expand_session(self, *, operation_id: str | None = None) -> None:
        with self._lock:
            self.flush_pending()
            if self.active_session_id is None:
                raise AttendanceError("Сначала откройте регистрацию")
            session_id = self.active_session_id
            operation = self._operation(
                "registration.expand", {"session_id": session_id}, operation_id
            )

            def action() -> JSONValue:
                self.service.redesign.expand_session(session_id)
                return None

            self.operations.execute(operation, action)
            session = self.service.redesign.get_session(session_id)
            self.active_lesson_id = min(session.target_pair_ids)
            self._active_pair_ids = session.target_pair_ids
            self._admission = _AdmissionContext(
                self.active_lesson_id,
                self.active_session_id,
                self._active_block_id,
                self._active_pair_ids,
            )
            self._events.append(ControllerEvent("changed"))

    def simulate(self, uid: str) -> None:
        present, remove = (
            getattr(self.reader, "present", None),
            getattr(self.reader, "remove", None),
        )
        if not callable(present) or not callable(remove):
            raise AttendanceError("Имитатор доступен только в отдельном демонстрационном режиме")
        remove()
        present(uid)
        remove()

    def drain(self) -> list[ControllerEvent]:
        with self._lock:
            self._flush()
            events = list(self._events)
            self._events.clear()
            return events

    def shutdown(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._accepting = False
            self._admission = None
        self.reader.stop()
        with self._lock:
            self.close_lesson()
            self._stopped = True
