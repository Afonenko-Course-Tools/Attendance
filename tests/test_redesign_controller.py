from datetime import UTC, date, datetime

import pytest
from controller_test_support import registration_service

from attendance_lab.application.controller import AttendanceController
from attendance_lab.infrastructure.readers import DemoReader


@pytest.fixture
def lab(tmp_path):
    service, first = registration_service(tmp_path / "synthetic-controller.sqlite3")
    second = service.redesign.schedule_block(first.roster_id, date(2026, 9, 8), 1)
    controller = AttendanceController(service, DemoReader)
    yield service, controller, first, second
    assert controller.retry_pending()
    controller.shutdown()
    service.close()


def test_scan_keeps_session_and_flushes_before_switch(lab):
    service, controller, first, second = lab
    session = controller.open_session(first.id, set(first.pair_ids))
    controller._capture("00:11", datetime.now(UTC))
    controller.open_session(second.id, {second.pair_ids[0]})
    scan = next(e.scan for e in controller.drain() if e.scan)
    assert scan.session_id == session and scan.lesson_id == first.pair_ids[0]
    assert scan.block_id == first.id and scan.target_pair_ids == first.pair_ids
    assert len(service.redesign.pair_evidence(first.pair_ids[0])) == 1
    assert not service.redesign.pair_evidence(second.pair_ids[0])


def test_close_and_expand_flush_before_transition(lab):
    service, controller, first, _ = lab
    session = controller.open_session(first.id, {first.pair_ids[0]})
    controller._capture("0011", datetime.now(UTC))
    controller.expand_session()
    assert service.redesign.get_session(session).target_pair_ids == first.pair_ids
    assert len(service.redesign.pair_evidence(first.pair_ids[0])) == 1
    controller._capture("0022", datetime.now(UTC))
    controller.close_session()
    assert controller.active_session_id is None
    assert service.redesign.get_session(session).closed_at is not None
    assert len(service.redesign.pair_evidence(first.pair_ids[0])) == 2


def test_failed_commit_retains_private_scan_context_without_success(lab, monkeypatch):
    service, controller, first, _ = lab
    controller.open_session(first.id, {first.pair_ids[0]})
    controller.drain()
    original = service.redesign.record_session_scan

    def fail(*args):
        raise OSError("failure")

    monkeypatch.setattr(service.redesign, "record_session_scan", fail)
    controller._capture("0011", datetime.now(UTC))
    events = controller.drain()
    assert not any(e.kind == "changed" and e.scan for e in events)
    failed = next(e for e in events if e.kind == "failed")
    assert failed.scan is not None and failed.scan.uid == "0011"
    assert [e.result.kind for e in events if e.kind == "public"] == ["error"]
    monkeypatch.setattr(service.redesign, "record_session_scan", original)
