"""Serialize captured card events with explicit lesson changes, without a GUI dependency."""

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import PublicResult
from attendance_lab.domain.rules import normalize_uid

logger = logging.getLogger(__name__)


class Reader(Protocol):
    def list_readers(self) -> list[str]: ...
    def start(self, name: str) -> None: ...
    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ControllerEvent:
    kind: Literal["public", "status", "changed"]
    result: PublicResult | None = None
    text: str = ""
    scan: CapturedScan | None = None


@dataclass(frozen=True, slots=True)
class CapturedScan:
    lesson_id: int
    uid: str
    at: datetime


class AttendanceController:
    """Reader callbacks enqueue only. All service calls happen on the calling/UI thread.

    The same lock protects capturing a context and draining before a transition.
    Therefore a late callback either belongs to the old lesson and is committed
    before its closure, or captures the new context; it cannot be relabelled.
    """

    def __init__(
        self,
        service: AttendanceService,
        reader_factory: Callable[[Callable[[str, datetime], None], Callable[[str], None]], Reader],
    ) -> None:
        self.service = service
        self._lock = RLock()
        self._pending: deque[CapturedScan] = deque()
        self._events: deque[ControllerEvent] = deque()
        self.active_lesson_id: int | None = None
        self._stopped = False
        self.reader = reader_factory(self._capture, self._status)

    def _capture(self, uid: str, at: datetime) -> None:
        with self._lock:
            if self._stopped:
                return
            if self.active_lesson_id is None:
                self._events.append(ControllerEvent("status", text="Регистрация закрыта"))
                return
            self._pending.append(CapturedScan(self.active_lesson_id, uid, at))

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
        # Never join a reader while holding the callback lock.
        self.reader.stop()
        self.reader.start(name)

    def _flush(self) -> None:
        while self._pending:
            scan = self._pending.popleft()
            try:
                scan = CapturedScan(scan.lesson_id, normalize_uid(scan.uid), scan.at)
                result = self.service.record_scan(scan.lesson_id, scan.uid, scan.at)
            except Exception as exc:
                logger.exception("Card registration failed for lesson %s", scan.lesson_id)
                self._events.append(ControllerEvent("status", text=f"Ошибка регистрации: {exc}"))
                result = PublicResult(
                    "error", "Не удалось сохранить отметку. Уберите карту и повторите попытку."
                )
            else:
                # Private feedback carries hardware context separately from the public DTO.
                self._events.append(ControllerEvent("changed", result=result, scan=scan))
            self._events.append(ControllerEvent("public", result=result))

    def open_lesson(self, lesson_id: int) -> None:
        with self._lock:
            self._flush()
            self.service.open_lesson(lesson_id)
            self.active_lesson_id = lesson_id
            self._events.extend(
                [
                    ControllerEvent("changed"),
                    ControllerEvent("public", PublicResult("idle", "Приложите карту")),
                ]
            )

    def close_lesson(self) -> None:
        with self._lock:
            self._flush()
            if self.active_lesson_id is not None:
                self.service.close_lesson(self.active_lesson_id)
                self.active_lesson_id = None
            self._events.extend(
                [
                    ControllerEvent("changed"),
                    ControllerEvent("public", PublicResult("closed", "Регистрация закрыта")),
                ]
            )

    def simulate(self, uid: str) -> None:
        # Capability is only installed by the demo adapter; real readers cannot be injected.
        present = getattr(self.reader, "present", None)
        remove = getattr(self.reader, "remove", None)
        if not callable(present) or not callable(remove):
            raise ValueError("Имитатор доступен только в отдельном демонстрационном режиме")
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
        if self._stopped:
            return
        self.reader.stop()
        with self._lock:
            self.close_lesson()
            self._stopped = True
