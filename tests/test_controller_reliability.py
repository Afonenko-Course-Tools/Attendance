"""Real temporary SQLite exercises FIFO failure preservation and close admission barriers."""

from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Thread

import pytest
from controller_test_support import registration_service

from attendance_lab.application.controller import AttendanceController, PendingScansError
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def lab(tmp_path):
    service, block = registration_service(tmp_path / "synthetic-reliable.sqlite3")
    controller = AttendanceController(service, DemoReader)
    controller.connect_reader(controller.available_readers()[0])
    session = controller.open_session(block.id, {block.pair_ids[0]}, operation_id="open-first")
    controller.drain()
    yield service, controller, block, session
    assert not controller.blocked and controller.pending_count == 0
    controller.shutdown()
    service.close()


def test_failed_head_remains_fifo_and_periodic_drain_never_retries(lab, monkeypatch):
    service, controller, block, session = lab
    original = service.repository.put_operation_receipt
    calls = []

    def fail(receipt):
        calls.append(receipt.operation_id)
        raise OSError("synthetic rollback")

    with monkeypatch.context() as patch:
        patch.setattr(service.repository, "put_operation_receipt", fail)
        controller._capture("0011", datetime.now(UTC))
        controller._capture("0022", datetime.now(UTC))
        events = controller.drain()
        failed = next(e for e in events if e.kind == "failed")
        assert failed.scan.session_id == session
        assert failed.scan.block_id == block.id
        assert failed.scan.target_pair_ids == (block.pair_ids[0],)
        assert controller.pending_count == 2 and controller.blocked
        assert not service.redesign.pair_evidence(block.pair_ids[0])
        assert controller.drain() == []
        assert len(calls) == 1
    saved = []

    def spy(receipt):
        saved.append(receipt.operation_id)
        original(receipt)

    monkeypatch.setattr(service.repository, "put_operation_receipt", spy)
    assert controller.retry_pending()
    changes = [e for e in controller.drain() if e.kind == "changed" and e.scan]
    assert [e.scan.uid for e in changes] == ["0011", "0022"]
    assert saved[0] == failed.scan.operation_id
    assert len(set(saved)) == 2
    assert [e.result.kind for e in changes] == ["success", "success"]


def test_close_bars_admissions_before_flush_and_keeps_context_on_rollback(lab, monkeypatch):
    service, controller, block, session = lab
    controller._capture("0011", datetime.now(UTC))
    original = service.redesign.record_session_scan

    def fail_with_late_callback(*args):
        controller._capture("0022", datetime.now(UTC))
        original(*args)
        raise OSError("synthetic failure after domain changes but before commit")

    with monkeypatch.context() as patch:
        patch.setattr(service.redesign, "record_session_scan", fail_with_late_callback)
        with pytest.raises(PendingScansError):
            controller.close_session(operation_id="close-first", session_id=session)
    assert not controller.accepting and controller.blocked
    assert controller.active_session_id == session and controller.pending_count == 1
    assert service.redesign.get_session(session).closed_at is None
    assert not service.redesign.pair_evidence(block.pair_ids[0])
    assert not any(e.result and e.result.kind == "closed" for e in controller.drain())
    assert controller.retry_pending()
    assert not controller.accepting
    controller._capture("0033", datetime.now(UTC))
    assert controller.pending_count == 0
    controller.close_session(operation_id="close-first", session_id=session)
    assert controller.active_session_id is None
    assert service.redesign.get_session(session).closed_at is not None
    assert [e.uid for e in service.redesign.pair_evidence(block.pair_ids[0])] == ["0011"]


def test_unknown_commit_reconciles_receipt_before_any_scan_retry(lab, monkeypatch):
    service, controller, block, _ = lab
    original_transaction = service.repository.operation_transaction
    lost = True

    @contextmanager
    def lose_reply():
        nonlocal lost
        with original_transaction():
            yield
        if lost:
            lost = False
            raise OSError("synthetic committed response lost")

    monkeypatch.setattr(service.repository, "operation_transaction", lose_reply)
    controller._capture("0011", datetime.now(UTC))
    failed = next(e for e in controller.drain() if e.kind == "failed")
    assert controller.pending_count == 1 and controller.blocked
    assert len(service.redesign.pair_evidence(block.pair_ids[0])) == 1
    assert service.repository.get_operation_receipt(failed.scan.operation_id) is not None

    def forbidden(*args):
        raise AssertionError("the committed scan must not be applied twice")

    monkeypatch.setattr(service.redesign, "record_session_scan", forbidden)
    assert controller.retry_pending()
    changed = next(e for e in controller.drain() if e.kind == "changed" and e.scan)
    assert changed.scan.operation_id == failed.scan.operation_id
    assert changed.result.kind == "success"  # Exact first result, never reclassified as repeat.
    assert len(service.redesign.pair_evidence(block.pair_ids[0])) == 1


def test_open_replay_after_restart_and_recovery_never_reopens_old_session(tmp_path):
    path = tmp_path / "synthetic-open-replay.sqlite3"
    service, block = registration_service(path)
    controller = AttendanceController(service, DemoReader)
    first = controller.open_session(block.id, {block.pair_ids[0]}, operation_id="open-stable")
    # A crash loses the reply. Recovery closes the session before replaying its command.
    service.close()
    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    service.recover_interrupted()
    controller = AttendanceController(service, DemoReader)
    try:
        repeated = controller.open_session(
            block.id, {block.pair_ids[0]}, operation_id="open-stable"
        )
        assert repeated == first
        assert service.redesign.get_session(first).closed_at is not None
        assert controller.active_session_id is None and not controller.accepting
        assert len(service.redesign.store.rd_sessions()) == 1
        with pytest.raises(AttendanceError, match="другими параметрами"):
            controller.open_session(block.id, set(block.pair_ids), operation_id="open-stable")
    finally:
        controller.shutdown()
        service.close()


def test_close_reply_replay_after_restart_keeps_new_session_open(tmp_path):
    path = tmp_path / "synthetic-close-replay.sqlite3"
    service, block = registration_service(path)
    controller = AttendanceController(service, DemoReader)
    first = controller.open_session(block.id, {block.pair_ids[0]})
    controller.close_session(operation_id="close-stable", session_id=first)
    service.close()
    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    controller = AttendanceController(service, DemoReader)
    try:
        second = controller.open_session(block.id, {block.pair_ids[1]})
        controller.close_session(operation_id="close-stable", session_id=first)
        assert controller.active_session_id == second and controller.accepting
        assert service.redesign.get_session(second).closed_at is None
        assert service.redesign.get_session(first).closed_at is not None
    finally:
        controller.shutdown()
        service.close()


def test_failed_shutdown_retains_queue_and_can_be_explicitly_retried(lab, monkeypatch):
    service, controller, block, session = lab
    controller._capture("0011", datetime.now(UTC))

    def fail(*args):
        raise OSError("synthetic write failure")

    with monkeypatch.context() as patch:
        patch.setattr(service.redesign, "record_session_scan", fail)
        with pytest.raises(PendingScansError):
            controller.shutdown()
    assert controller.active_session_id == session
    assert controller.pending_count == 1 and not controller.accepting
    assert controller.retry_pending()
    controller.shutdown()
    assert service.redesign.get_session(session).closed_at is not None
    assert len(service.redesign.pair_evidence(block.pair_ids[0])) == 1


def test_reader_thread_only_captures_immutable_context_and_never_uses_sqlite(lab):
    service, controller, block, session = lab
    producer = Thread(target=controller._capture, args=("0011", datetime.now(UTC)))
    producer.start()
    producer.join(timeout=1)
    assert not producer.is_alive()
    assert controller.pending_count == 1
    assert not service.redesign.pair_evidence(block.pair_ids[0])
    event = next(e for e in controller.drain() if e.kind == "changed" and e.scan)
    assert event.scan.session_id == session and event.scan.operation_id


def test_callback_waiting_on_transition_cannot_capture_next_sessions_context(lab):
    from threading import Event, get_ident

    service, controller, block, _ = lab
    original_lock = controller._lock
    caller = get_ident()
    waiting = Event()

    class ObservedLock:
        def __enter__(self):
            if get_ident() != caller:
                waiting.set()
            return original_lock.__enter__()

        def __exit__(self, *args):
            return original_lock.__exit__(*args)

    controller._lock = ObservedLock()
    with controller._lock:
        producer = Thread(target=controller._capture, args=("00AA", datetime.now(UTC)))
        producer.start()
        assert waiting.wait(1), "reader must be waiting after capturing its admission token"
        new_session = controller.open_session(block.id, {block.pair_ids[1]})
    producer.join(timeout=1)
    assert not producer.is_alive()
    assert controller.active_session_id == new_session
    assert controller.pending_count == 0
    assert not service.redesign.pair_evidence(block.pair_ids[0])
    assert not service.redesign.pair_evidence(block.pair_ids[1])


def test_committed_scan_lost_reply_reconciles_after_restart_even_after_session_recovery(tmp_path):
    path = tmp_path / "synthetic-scan-restart.sqlite3"
    service, block = registration_service(path)
    controller = AttendanceController(service, DemoReader)
    controller.open_session(block.id, {block.pair_ids[0]})
    original = service.repository.operation_transaction

    @contextmanager
    def lost_reply():
        with original():
            yield
        raise OSError("synthetic lost response after commit")

    service.repository.operation_transaction = lost_reply
    controller._capture("00BB", datetime.now(UTC))
    failed = next(event for event in controller.drain() if event.kind == "failed")
    captured = failed.scan
    service.close()  # Simulated process loss before an explicit retry.
    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    service.recover_interrupted()
    resumed = AttendanceController(service, DemoReader)
    try:
        assert service.redesign.get_session(captured.session_id).closed_at is not None
        result = resumed._record(captured)
        assert result.kind == "success" and result.at == captured.at
        assert len(service.redesign.pair_evidence(block.pair_ids[0])) == 1
    finally:
        resumed.shutdown()
        service.close()
