from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from attendance_lab.application.controller import AttendanceController
from attendance_lab.domain.models import PublicResult
from attendance_lab.infrastructure.readers import DemoReader


def make_controller():
    redesign = Mock()
    redesign.open_session.side_effect = lambda block, pairs: SimpleNamespace(
        id=block, target_pair_ids=tuple(sorted(pairs))
    )
    redesign.record_session_scan.return_value = PublicResult("success", "")
    service = SimpleNamespace(redesign=redesign, record_scan=Mock(), close_lesson=Mock())
    return AttendanceController(service, DemoReader), redesign


def test_scan_keeps_session_and_flushes_before_switch():
    controller, api = make_controller()
    controller.open_session(1, {10, 11})
    controller._capture("00:11", datetime.now(UTC))
    controller.open_session(2, {12})
    scan = next(e.scan for e in controller.drain() if e.scan)
    assert scan.session_id == 1 and scan.lesson_id == 10
    assert api.record_session_scan.call_args.args[0] == 1
    assert controller.active_session_id == 2
    controller.service.record_scan.assert_not_called()


def test_close_and_expand_flush_before_transition():
    controller, api = make_controller()
    controller.open_session(1, {10})
    controller._capture("0011", datetime.now(UTC))
    api.expand_session.return_value = SimpleNamespace(target_pair_ids=(10, 11))
    controller.expand_session()
    assert [c[0] for c in api.mock_calls][-2:] == ["record_session_scan", "expand_session"]
    controller._capture("0022", datetime.now(UTC))
    controller.close_session()
    assert controller.active_session_id is None
    assert [c[0] for c in api.mock_calls][-2:] == ["record_session_scan", "close_session"]


def test_failed_commit_has_no_private_scan_event():
    controller, api = make_controller()
    controller.open_session(1, {10})
    controller.drain()
    api.record_session_scan.side_effect = OSError("failure")
    controller._capture("0011", datetime.now(UTC))
    events = controller.drain()
    assert not any(e.scan for e in events)
    assert [e.result.kind for e in events if e.kind == "public"] == ["error"]
