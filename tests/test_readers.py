"""Exercise the native wrapper and reader thread using injected PC/SC calls."""

from __future__ import annotations

from collections.abc import Callable
from threading import Condition, Event
from time import monotonic

import pytest

from attendance_lab.infrastructure.readers import (
    DEMO_READER_NAME,
    GET_UID,
    DemoReader,
    PCSCReader,
    PyscardBackend,
    ReaderError,
)

TARGET = "ACS ACR1281 1S Dual Reader 00 01"
OTHER = "ACS ACR1281 1S Dual Reader 00 00"


class NativePCSC:
    """Thread-safe fake of native functions, not of the reader state machine."""

    SCARD_S_SUCCESS = 0
    SCARD_E_NO_READERS_AVAILABLE = 1
    SCARD_E_TIMEOUT = 2
    SCARD_SCOPE_USER = 0
    SCARD_SHARE_SHARED = 2
    SCARD_PROTOCOL_T1 = 2
    SCARD_LEAVE_CARD = 0
    SCARD_STATE_UNAWARE = 0
    SCARD_STATE_CHANGED = 2
    SCARD_STATE_UNKNOWN = 4
    SCARD_STATE_UNAVAILABLE = 8
    SCARD_STATE_EMPTY = 16
    SCARD_STATE_PRESENT = 32
    SCARD_STATE_INUSE = 256

    def __init__(self) -> None:
        self.condition = Condition()
        self.readers = [OTHER, TARGET]
        self.present = False
        self.inuse = False
        self.generation = 0
        self.response = [0, 4, 0xFA, 0x10, 0, 0x12, 0xFE, 0x90, 0]
        self.connections: list[tuple[str, int, int]] = []
        self.apdus: list[tuple[int, list[int]]] = []
        self.dispositions: list[int] = []
        self.wait_names: set[str] = set()
        self.released_contexts = 0
        self.contexts = 0
        self.wait_count = 0
        self.block_transmit: Event | None = None
        self.transmit_entered = Event()

    def card(self, present: bool) -> None:
        with self.condition:
            if self.present != present:
                self.generation = (self.generation + 1) & 0xFFFF
                self.present = present
                self.inuse = False
            self.condition.notify_all()

    def reinsert_between_polls(self) -> None:
        with self.condition:
            assert self.present
            self.generation = (self.generation + 2) & 0xFFFF
            self.condition.notify_all()

    def device(self, connected: bool) -> None:
        with self.condition:
            self.readers = [OTHER, TARGET] if connected else [OTHER]
            self.condition.notify_all()

    def SCardEstablishContext(self, scope: int) -> tuple[int, int]:
        self.contexts += 1
        return self.SCARD_S_SUCCESS, self.contexts

    def SCardListReaders(self, context: int, groups: list) -> tuple[int, list[str]]:
        return self.SCARD_S_SUCCESS, self.readers.copy()

    def SCardGetStatusChange(self, context: int, timeout_ms: int, states: list) -> tuple[int, list]:
        name, old = states[0]
        assert len(states) == 1
        assert 0 < timeout_ms <= 200
        with self.condition:
            self.wait_count += 1
            self.wait_names.add(name)
            self.condition.notify_all()

            def state() -> int:
                if name not in self.readers:
                    return self.SCARD_STATE_UNKNOWN
                flags = self.SCARD_STATE_PRESENT if self.present else self.SCARD_STATE_EMPTY
                if self.inuse:
                    flags |= self.SCARD_STATE_INUSE
                return (self.generation << 16) | flags

            changed = self.condition.wait_for(lambda: old != state(), timeout=timeout_ms / 1000)
            if not changed:
                return self.SCARD_E_TIMEOUT, []
            # ATR deliberately identical for all presentations; never UID.
            return self.SCARD_S_SUCCESS, [(name, state() | self.SCARD_STATE_CHANGED, [0x3B])]

    def SCardConnect(
        self, context: int, name: str, share: int, protocol: int
    ) -> tuple[int, int, int]:
        assert name == TARGET
        with self.condition:
            self.connections.append((name, share, protocol))
            self.inuse = True
            self.condition.notify_all()
        return self.SCARD_S_SUCCESS, len(self.connections), protocol

    def SCardTransmit(self, card: int, protocol: int, apdu: list[int]) -> tuple[int, list[int]]:
        self.transmit_entered.set()
        if self.block_transmit is not None:
            self.block_transmit.wait(timeout=3)
        with self.condition:
            self.apdus.append((protocol, apdu))
            self.condition.notify_all()
        return self.SCARD_S_SUCCESS, self.response.copy()

    def SCardDisconnect(self, card: int, disposition: int) -> int:
        with self.condition:
            self.dispositions.append(disposition)
            self.inuse = False
            self.condition.notify_all()
        return self.SCARD_S_SUCCESS

    def SCardReleaseContext(self, context: int) -> int:
        with self.condition:
            self.released_contexts += 1
            self.condition.notify_all()
        return self.SCARD_S_SUCCESS

    def SCardGetErrorMessage(self, result: int) -> str:
        return f"Native PC/SC error {result}"


def await_condition(native: NativePCSC, predicate: Callable[[], bool]) -> None:
    with native.condition:
        assert native.condition.wait_for(predicate, timeout=1.5)


def make_reader(native: NativePCSC, cards: list, statuses: list, **kwargs) -> PCSCReader:
    def on_card(uid, at):
        with native.condition:
            cards.append((uid, at))
            native.condition.notify_all()

    def on_status(text):
        with native.condition:
            statuses.append(text)
            native.condition.notify_all()

    return PCSCReader(
        on_card,
        on_status,
        backend_factory=lambda: PyscardBackend(native),
        poll_interval=0.01,
        reconnect_interval=0.02,
        read_retry_interval=0.02,
        **kwargs,
    )


def test_native_selection_persistent_connection_and_held_card() -> None:
    native, cards, statuses = NativePCSC(), [], []
    reader = make_reader(native, cards, statuses)
    assert reader.list_readers() == [OTHER, TARGET]
    reader.start(TARGET)
    try:
        native.card(True)
        await_condition(native, lambda: len(cards) == 1)
        observed_waits = native.wait_count
        await_condition(native, lambda: native.wait_count >= observed_waits + 5)
        assert len(cards) == 1
        assert len(native.connections) == 1
        assert native.dispositions == []
        assert native.apdus == [(native.SCARD_PROTOCOL_T1, list(GET_UID))]
        assert native.connections[0] == (
            TARGET,
            native.SCARD_SHARE_SHARED,
            native.SCARD_PROTOCOL_T1,
        )
        assert native.wait_names == {TARGET}
        assert cards[0][0] == "0004FA100012FE"
        assert cards[0][1].utcoffset() is not None
        native.card(False)
        await_condition(native, lambda: len(native.dispositions) == 1)
        native.card(True)
        await_condition(native, lambda: len(cards) == 2)
        assert cards[1][0] == cards[0][0]
    finally:
        reader.stop()
    assert native.dispositions == [native.SCARD_LEAVE_CARD] * 2
    assert native.released_contexts == native.contexts
    assert all(cards[0][0] not in status for status in statuses)


def test_fast_reinsertion_detected_by_native_event_counter() -> None:
    native, cards, statuses = NativePCSC(), [], []
    reader = make_reader(native, cards, statuses)
    reader.start(TARGET)
    try:
        native.card(True)
        await_condition(native, lambda: len(cards) == 1)
        native.reinsert_between_polls()
        await_condition(native, lambda: len(cards) == 2)
        assert len(native.connections) == 2
    finally:
        reader.stop()


@pytest.mark.parametrize("response", [[0x6A, 0x81], [0x90, 0], [0x90]])
def test_invalid_apdu_response_no_success_and_bounded_retries(response) -> None:
    native, cards, statuses = NativePCSC(), [], []
    native.response = response
    reader = make_reader(native, cards, statuses)
    reader.start(TARGET)
    try:
        native.card(True)
        await_condition(native, lambda: len(native.apdus) == 3)
        observed_waits = native.wait_count
        await_condition(native, lambda: native.wait_count >= observed_waits + 6)
        assert cards == []
        assert len(native.apdus) == 3
        assert sum("Ошибка чтения" in text for text in statuses) == 3
        native.card(False)
        await_condition(native, lambda: statuses[-1].endswith("Приложите карту."))
        native.response = [0, 1, 0x90, 0]
        native.card(True)
        await_condition(native, lambda: len(cards) == 1)
        assert cards[0][0] == "0001"
    finally:
        reader.stop()


def test_reader_disconnect_reconnect_keeps_selected_interface() -> None:
    native, cards, statuses = NativePCSC(), [], []
    reader = make_reader(native, cards, statuses)
    reader.start(TARGET)
    try:
        native.card(True)
        await_condition(native, lambda: len(cards) == 1)
        native.device(False)
        await_condition(native, lambda: any("отключён" in s for s in statuses))
        native.card(False)
        native.device(True)
        await_condition(native, lambda: statuses[-1].endswith("Приложите карту."))
        native.card(True)
        await_condition(native, lambda: len(cards) == 2)
        assert {connection[0] for connection in native.connections} == {TARGET}
    finally:
        reader.stop()


def test_missing_native_dependency_nonfatal_and_private(monkeypatch) -> None:
    def unavailable(name):
        raise ImportError("no smartcard module")

    monkeypatch.setattr(
        "attendance_lab.infrastructure.readers.importlib.import_module", unavailable
    )
    cards, statuses = [], []
    reader = PCSCReader(lambda *args: cards.append(args), statuses.append)
    assert reader.list_readers() == []
    assert "pyscard" in statuses[-1]
    assert cards == []


def test_stop_is_bounded_and_suppresses_late_driver_result() -> None:
    native, cards, statuses = NativePCSC(), [], []
    release = Event()
    native.block_transmit = release
    reader = make_reader(native, cards, statuses, join_timeout=0.05)
    reader.start(TARGET)
    native.card(True)
    assert native.transmit_entered.wait(timeout=1)
    before = monotonic()
    reader.stop()
    assert monotonic() - before < 0.5
    assert cards == []
    with pytest.raises(ReaderError, match="Предыдущее"):
        reader.start(TARGET)
    release.set()
    await_condition(native, lambda: native.released_contexts == native.contexts)
    reader.stop()
    assert cards == []


def test_demo_requires_selection_and_removal_and_preserves_uid() -> None:
    cards, statuses = [], []
    reader = DemoReader(lambda *args: cards.append(args), statuses.append)
    assert reader.list_readers() == [DEMO_READER_NAME]
    with pytest.raises(ReaderError):
        reader.present("0012")
    with pytest.raises(ReaderError):
        reader.start("first reader")
    reader.start(DEMO_READER_NAME)
    reader.present("00:12:00:ff:04:ab:01")
    reader.present("00:12:00:ff:04:ab:01")
    assert len(cards) == 1
    assert cards[0][0] == "001200FF04AB01"
    assert cards[0][1].utcoffset() is not None
    reader.remove()
    reader.present("00:12:00:ff:04:ab:01")
    assert len(cards) == 2
    reader.stop()
    with pytest.raises(ReaderError):
        reader.present("0012")


@pytest.mark.parametrize("uid", ["", " ", "ABC", "GG00"])
def test_demo_rejects_invalid_uid(uid) -> None:
    reader = DemoReader(lambda *_: None, lambda _: None)
    reader.start(DEMO_READER_NAME)
    with pytest.raises(ReaderError):
        reader.present(uid)
