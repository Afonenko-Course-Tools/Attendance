from datetime import UTC, datetime, timedelta

import pytest

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import PublicResult
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def setup(tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "demo.sqlite3", mode="demo"))
    discipline = service.save_discipline("Физика")
    at = datetime.now(UTC)
    one = service.save_lesson(discipline.id, at, at + timedelta(hours=1), "UTC")
    two = service.save_lesson(
        discipline.id, at + timedelta(hours=2), at + timedelta(hours=3), "UTC"
    )
    controller = AttendanceController(service, DemoReader)
    controller.connect_reader(controller.available_readers()[0])
    yield service, controller, one, two
    controller.shutdown()
    service.close()


def test_queued_scan_keeps_context_on_switch(setup):
    service, controller, first, second = setup
    controller.open_lesson(first.id)
    controller.simulate("00:12:AB:CD")
    # Card has been captured, but periodic GUI drain has not occurred yet.
    controller.open_lesson(second.id)
    controller.drain()
    assert [r.uid for r in service.lesson_log(first.id)] == ["0012ABCD"]
    assert service.lesson_log(second.id) == []


def test_capture_stops_on_close_and_flushes_accepted_card(setup):
    service, controller, first, _ = setup
    controller.open_lesson(first.id)
    controller.simulate("0012ABCD")
    controller.close_lesson()
    controller.simulate("0012ABCE")
    controller.drain()
    assert len(service.lesson_log(first.id)) == 1


def test_store_failure_never_becomes_public_success(setup, monkeypatch):
    service, controller, first, _ = setup
    controller.open_lesson(first.id)
    controller.drain()

    def fail(*args):
        raise OSError("private UID 0012ABCD /private/database.sqlite3")

    monkeypatch.setattr(service, "record_scan", fail)
    controller.simulate("0012ABCD")
    results = [e.result for e in controller.drain() if e.kind == "public"]
    assert len(results) == 1
    assert results[0].kind == "error"
    assert "0012ABCD" not in str(results[0])
    assert "/private" not in str(results[0])
    assert service.lesson_log(first.id) == []


def test_public_result_has_no_hardware_fields():
    from dataclasses import fields

    assert {field.name for field in fields(PublicResult)} == {"kind", "message", "full_name", "at"}


def test_private_scan_feedback_is_committed_normalized_and_lesson_specific(setup):
    _, controller, first, second = setup
    controller.open_lesson(first.id)
    controller.drain()
    at = datetime.now(UTC)
    controller._capture("00:12:AB:CD", at)
    controller._capture("00:12:AB:CD", at)
    events = controller.drain()
    private = [event for event in events if event.scan is not None]
    assert [event.result.kind for event in private] == ["success", "repeat"]
    assert all(event.kind == "changed" and event.scan.uid == "0012ABCD" for event in private)
    assert all(event.scan.lesson_id == first.id for event in private)
    assert all(event.scan is None for event in events if event.kind == "public")
    controller.open_lesson(second.id)
    controller._capture("0012ABCD", at)
    latest = next(event for event in controller.drain() if event.scan is not None)
    assert latest.scan.lesson_id == second.id and latest.result.kind == "success"
