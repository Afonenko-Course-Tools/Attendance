"""PC/SC and demo reader adapters. Nothing in this module imports Qt.

The native wrapper is loaded on demand: demo mode does not require pyscard.
One persistent T=1 connection is used for one card presentation. Waiting for
card events never transmits APDUs or resets the card. PC/SC's event counter also
detects a removal/reinsertion that occurs between two status requests on Linux.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from typing import Any, Protocol

GET_UID = (0xFF, 0xCA, 0x00, 0x00, 0x00)
DEMO_READER_NAME = "Имитатор карты (тестовая база)"


class ReaderError(RuntimeError):
    """Technical reader error; only the private operator view may display it."""


class ReaderUnavailable(ReaderError):
    """A reader/resource-manager failure requiring a fresh PC/SC context."""


@dataclass(frozen=True, slots=True)
class ReaderState:
    native_state: int
    present: bool
    available: bool = True

    @property
    def generation(self) -> int:
        return (self.native_state >> 16) & 0xFFFF


class ReaderBackend(Protocol):
    """Native operations injected in adapter tests; owned by the reader thread."""

    def open(self) -> None: ...

    def list_readers(self) -> list[str]: ...

    def wait(
        self, name: str, previous: ReaderState | None, timeout_ms: int
    ) -> ReaderState | None: ...

    def connect(self, name: str) -> None: ...

    def read_uid(self) -> str: ...

    def disconnect(self) -> None: ...

    def close(self) -> None: ...


class PyscardBackend:
    """Small wrapper over pyscard.scard; only GET UID is ever transmitted.

    ``native`` is an injection point for tests of the actual wrapper API. All
    handles are local to this backend and closed in the owning worker thread.
    """

    def __init__(self, native: Any = None) -> None:
        self._native = native
        self._context: Any = None
        self._card: Any = None
        self._protocol: int | None = None

    def open(self) -> None:
        if self._native is None:
            try:
                self._native = importlib.import_module("smartcard.scard")
            except (ImportError, OSError) as exc:
                raise ReaderUnavailable(
                    "Модуль pyscard или библиотека PC/SC недоступны. "
                    "Установите аппаратные зависимости из инструкции; "
                    "имитатор работает без них."
                ) from exc
        native = self._native
        result, context = native.SCardEstablishContext(native.SCARD_SCOPE_USER)
        self._check(result, "Подключение к службе PC/SC", unavailable=True)
        self._context = context

    def _check(self, result: int, operation: str, *, unavailable: bool = False) -> None:
        if result == self._native.SCARD_S_SUCCESS:
            return
        message = self._native.SCardGetErrorMessage(result)
        error_type = ReaderUnavailable if unavailable else ReaderError
        raise error_type(f"{operation}: {message}")

    def list_readers(self) -> list[str]:
        native = self._native
        result, names = native.SCardListReaders(self._context, [])
        if result == native.SCARD_E_NO_READERS_AVAILABLE:
            return []
        self._check(result, "Список интерфейсов PC/SC", unavailable=True)
        return [str(name) for name in names]

    def wait(self, name: str, previous: ReaderState | None, timeout_ms: int) -> ReaderState | None:
        native = self._native
        state = previous.native_state if previous else native.SCARD_STATE_UNAWARE
        result, states = native.SCardGetStatusChange(self._context, timeout_ms, [(name, state)])
        if result == native.SCARD_E_TIMEOUT:
            return None
        self._check(result, "Состояние считывателя", unavailable=True)
        if len(states) != 1 or str(states[0][0]) != name:
            raise ReaderUnavailable("PC/SC вернул состояние другого интерфейса.")
        event_state = int(states[0][1]) & ~native.SCARD_STATE_CHANGED
        unavailable = native.SCARD_STATE_UNKNOWN | native.SCARD_STATE_UNAVAILABLE
        return ReaderState(
            native_state=event_state,
            present=bool(event_state & native.SCARD_STATE_PRESENT),
            available=not bool(event_state & unavailable),
        )

    def connect(self, name: str) -> None:
        if self._card is not None:
            return
        native = self._native
        result, card, protocol = native.SCardConnect(
            self._context, name, native.SCARD_SHARE_SHARED, native.SCARD_PROTOCOL_T1
        )
        self._check(result, "Подключение к карте по T=1")
        self._card = card
        self._protocol = protocol

    def read_uid(self) -> str:
        if self._card is None:
            raise ReaderError("Нет активного соединения с картой.")
        result, response = self._native.SCardTransmit(self._card, self._protocol, list(GET_UID))
        self._check(result, "Чтение UID")
        if len(response) < 2:
            raise ReaderError("Неполный ответ на команду чтения UID.")
        if tuple(response[-2:]) != (0x90, 0x00):
            status = "".join(f"{value:02X}" for value in response[-2:])
            raise ReaderError(f"Команда чтения UID отклонена: SW={status}.")
        if len(response) == 2:
            raise ReaderError("Карта вернула пустой UID.")
        try:
            # Byte order and every leading zero are preserved; ATR is unused.
            return bytes(response[:-2]).hex().upper()
        except (ValueError, TypeError) as exc:
            raise ReaderError("Ответ UID содержит некорректные байты.") from exc

    def disconnect(self) -> None:
        if self._card is None:
            return
        card, self._card = self._card, None
        self._protocol = None
        # Never reset or power down the card during normal polling/cleanup.
        self._native.SCardDisconnect(card, self._native.SCARD_LEAVE_CARD)

    def close(self) -> None:
        try:
            self.disconnect()
        finally:
            if self._context is not None:
                context, self._context = self._context, None
                self._native.SCardReleaseContext(context)


class PCSCReader:
    """Read one selected interface in a bounded-wait background thread.

    A disconnected reader reconnects automatically to the exact selected name.
    Native transmit calls are subject to driver timeouts; stop() itself always
    returns within join_timeout, suppresses late callbacks and rejects restart
    until any stalled old worker has exited.
    """

    def __init__(
        self,
        on_card: Callable[[str, datetime], None],
        on_status: Callable[[str], None],
        *,
        backend_factory: Callable[[], ReaderBackend] = PyscardBackend,
        poll_interval: float = 0.2,
        reconnect_interval: float = 1.0,
        read_retry_interval: float = 0.75,
        max_read_attempts: int = 3,
        join_timeout: float = 2.0,
    ) -> None:
        self._on_card = on_card
        self._on_status = on_status
        self._backend_factory = backend_factory
        self._poll_ms = max(1, int(poll_interval * 1000))
        self._reconnect_interval = max(0.01, reconnect_interval)
        self._read_retry_interval = max(0.01, read_retry_interval)
        self._max_read_attempts = max(1, max_read_attempts)
        self._join_timeout = max(0.01, join_timeout)
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._status_lock = Lock()
        self._last_status = ""

    def _status(self, message: str) -> None:
        with self._status_lock:
            if message == self._last_status:
                return
            self._last_status = message
        self._on_status(message)

    def list_readers(self) -> list[str]:
        backend: ReaderBackend | None = None
        try:
            backend = self._backend_factory()
            backend.open()
            return backend.list_readers()
        except Exception as exc:
            self._status(str(exc))
            return []
        finally:
            if backend is not None:
                self._close_backend(backend)

    def start(self, name: str) -> None:
        if not name or not name.strip():
            raise ReaderError("Выберите интерфейс считывателя по имени.")
        self.stop()
        if self._thread is not None and self._thread.is_alive():
            raise ReaderError("Предыдущее обращение к драйверу ещё завершается.")
        stop_event = Event()
        self._stop_event = stop_event
        self._thread = Thread(
            target=self._run,
            args=(name, stop_event),
            daemon=True,
            name="attendance-pcsc",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        if thread is not current_thread():
            thread.join(timeout=self._join_timeout)
        if thread.is_alive():
            self._status("Считывание остановлено; ожидается завершение вызова драйвера.")
        else:
            self._thread = None
            self._status("Считывание остановлено.")

    def _close_backend(self, backend: ReaderBackend) -> None:
        try:
            backend.close()
        except Exception:
            # A disconnected USB reader may reject cleanup of obsolete handles.
            pass

    def _run(self, name: str, stop_event: Event) -> None:
        while not stop_event.is_set():
            backend: ReaderBackend | None = None
            try:
                backend = self._backend_factory()
                backend.open()
                if name not in backend.list_readers():
                    raise ReaderUnavailable(f"Интерфейс недоступен: {name}. Ожидание подключения.")
                self._watch(backend, name, stop_event)
            except Exception as exc:
                if not stop_event.is_set():
                    self._status(f"Считыватель: {exc}")
            finally:
                if backend is not None:
                    self._close_backend(backend)
            stop_event.wait(self._reconnect_interval)

    def _watch(self, backend: ReaderBackend, name: str, stop_event: Event) -> None:
        previous: ReaderState | None = None
        emitted = False
        attempts = 0
        next_attempt = 0.0
        self._status(f"Считыватель подключён: {name}. Приложите карту.")
        while not stop_event.is_set():
            update = backend.wait(name, previous, self._poll_ms)
            if stop_event.is_set():
                break
            state = update if update is not None else previous
            if state is None:
                continue
            if not state.available:
                raise ReaderUnavailable(f"Интерфейс отключён: {name}.")
            # On pcsc-lite the upper 16 bits count insertions/removals. Changes
            # to low-bit INUSE/EXCLUSIVE flags must not create a new presentation.
            new_presentation = (
                previous is not None
                and previous.present
                and state.present
                and state.generation != previous.generation
            )
            if not state.present or new_presentation:
                backend.disconnect()
                emitted, attempts, next_attempt = False, 0, 0.0
            previous = state
            if not state.present:
                self._status(f"Считыватель подключён: {name}. Приложите карту.")
                continue
            if emitted or attempts >= self._max_read_attempts or monotonic() < next_attempt:
                continue
            try:
                backend.connect(name)
                uid = backend.read_uid()
            except ReaderUnavailable:
                raise
            except Exception as exc:
                attempts += 1
                backend.disconnect()
                next_attempt = monotonic() + self._read_retry_interval
                self._status(
                    f"Ошибка чтения ({attempts}/{self._max_read_attempts}): {exc} "
                    "Уберите карту и приложите её снова."
                )
                continue
            if stop_event.is_set():
                break
            # The callback queues a fact only. Database commit/public success
            # belong to the application layer, not to the hardware adapter.
            self._on_card(uid, datetime.now(UTC))
            emitted = True
            self._status("Карта прочитана. Уберите карту перед следующим предъявлением.")


class DemoReader:
    """Explicit private simulator with the same held-card semantics as PC/SC."""

    def __init__(
        self,
        on_card: Callable[[str, datetime], None],
        on_status: Callable[[str], None],
    ) -> None:
        self._on_card = on_card
        self._on_status = on_status
        self._started = False
        self._present = False

    def list_readers(self) -> list[str]:
        return [DEMO_READER_NAME]

    def start(self, name: str) -> None:
        if name != DEMO_READER_NAME:
            raise ReaderError("Выбран неизвестный интерфейс имитатора.")
        self._started = True
        self._present = False
        self._on_status("Имитатор подключён. Используется отдельная тестовая база.")

    def stop(self) -> None:
        self._started = False
        self._present = False

    def present(self, uid: str) -> None:
        if not self._started:
            raise ReaderError("Сначала подключите имитатор.")
        normalized = "".join(uid.split()).replace(":", "").replace("-", "")
        try:
            raw = bytes.fromhex(normalized)
        except ValueError as exc:
            raise ReaderError("UID должен содержать полные шестнадцатеричные байты.") from exc
        if not raw:
            raise ReaderError("UID не может быть пустым.")
        if self._present:
            return
        self._present = True
        self._on_card(raw.hex().upper(), datetime.now(UTC))

    def remove(self) -> None:
        self._present = False
