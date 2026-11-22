"""Production Quick scenarios use real temporary SQLite and queued owner-thread work."""

import json
import time
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from threading import get_ident

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QThread, Signal, Slot
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from quick_test_support import open_backend, seed_workspace
from shiboken6 import isValid

from attendance_lab.application.controller import PendingScansError
from attendance_lab.application.journal import DEFAULT_REASON
from attendance_lab.application.operations import Operation, canonical_json
from attendance_lab.application.quick_backend import ViewContext
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.readers import DEMO_READER_NAME
from attendance_lab.presentation.quick.workspace_worker import Request, WorkspaceWorker


def wait_for(predicate, timeout_ms=4000):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        assert time.monotonic() < deadline, "Production Quick worker did not reach expected state"
        QTest.qWait(5)
        # PySide's QTest wait can retain the GIL; let the Python storage worker run.
        time.sleep(0.001)


def command(backend, name, payload, identity=None):
    from uuid import uuid4

    return backend.command(Operation.create(identity or str(uuid4()), name, payload))


@pytest.fixture
def backend(tmp_path):
    path = tmp_path / "synthetic-quick.sqlite3"
    ids = seed_workspace(path)
    backend = open_backend(path)
    yield backend, ids, path
    assert backend.controller.retry_pending()
    backend.close()


def test_all_snapshots_cross_strict_json_boundary_with_immutable_view_context(backend):
    backend, ids, _ = backend
    context = ViewContext(ids["courses"][0], ids["lessons"][0], 1)
    with pytest.raises(FrozenInstanceError):
        context.course_id = ids["courses"][1]
    first = backend.snapshot(context)
    encoded = canonical_json(first)
    assert json.loads(encoded)["selected_course_id"] == context.course_id
    for course, lesson in zip(ids["courses"], ids["lessons"], strict=True):
        snapshot = backend.snapshot(ViewContext(course, lesson, 2))
        assert json.loads(canonical_json(snapshot))["selected_lesson_id"] == lesson
    assert first["selected_course_id"] == context.course_id
    invalid = backend.snapshot(ViewContext(-1, -1, 3))
    assert invalid["selected_course_id"] == ids["courses"][0]
    assert invalid["selected_lesson_id"] is None
    canonical_json(invalid)


def test_known_unknown_late_binding_after_close_preserves_times_and_restart(backend):
    backend, ids, path = backend
    course, lesson = ids["courses"][0], ids["lessons"][0]
    context = ViewContext(course, lesson, 1)
    session = command(backend, "registration.open", {"course_id": course, "lesson_id": lesson})[
        "session_id"
    ]
    command(backend, "system.connect_reader", {"name": DEMO_READER_NAME})
    command(backend, "system.simulate", {"uid": "0011"})
    command(backend, "system.simulate", {"uid": "0022"})
    backend.poll()
    successes = [result for result in backend.public_results() if result.kind == "success"]
    assert len(successes) == 2
    assert successes[0].full_name and successes[1].full_name is None
    before = backend.snapshot(context)["registration"]
    unknown = next(card for card in before["unknown"] if card["uid"] == "0022")
    times = [
        (row["pair_id"], row["first_seen"], row["last_seen"])
        for row in before["logs"]
        if row["uid"] == "0022"
    ]
    command(backend, "registration.close", {"session_id": session})
    payload = {
        "uid": "0022",
        "student_id": ids["late_student"],
        "expected_student_id": None,
        "binding_revision": unknown["binding_revision"],
        "correction": False,
    }
    original = command(backend, "registration.bind", payload, "late-bind")
    assert command(backend, "registration.bind", payload, "late-bind") == original
    after = backend.snapshot(context)["registration"]
    assert not after["open"] and not after["unknown"]
    assert [
        (row["pair_id"], row["first_seen"], row["last_seen"])
        for row in after["logs"]
        if row["uid"] == "0022"
    ] == times
    assert len(backend.service.repository.list_binding_changes("0022")) == 1
    backend.close()
    restarted = open_backend(path)
    try:
        persisted = restarted.snapshot(context)
        canonical_json(persisted)
        assert not persisted["registration"]["open"]
        assert any(
            row["id"] == ids["late_student"] and row["present"]
            for row in persisted["registration"]["rows"]
        )
        assert command(restarted, "registration.bind", payload, "late-bind") == original
        assert len(restarted.service.repository.list_binding_changes("0022")) == 1
    finally:
        restarted.close()


def test_view_change_with_queued_card_cannot_relocate_it(backend):
    backend, ids, _ = backend
    course, lesson = ids["courses"][0], ids["lessons"][0]
    session = command(backend, "registration.open", {"course_id": course, "lesson_id": lesson})[
        "session_id"
    ]
    backend.controller._capture("00:33", datetime.now(UTC))
    assert backend.controller.pending_count == 1
    changed_view = ViewContext(ids["courses"][1], ids["lessons"][1], 2)
    assert backend.snapshot(changed_view)["registration"]["active_session_id"] == session
    backend.poll()
    visible = backend.snapshot(changed_view)
    assert visible["selected_course_id"] == ids["courses"][1]
    assert visible["registration"]["logs"] == []
    original = backend.snapshot(ViewContext(course, lesson, 3))
    assert {row["uid"] for row in original["registration"]["logs"]} == {"0033"}
    assert original["registration"]["active_session_id"] == session


def test_bind_and_unbind_reject_stale_revision_after_owner_returns(backend):
    backend, ids, _ = backend
    service = backend.service
    history = service.repository.list_binding_changes("0011")
    original_revision = history[0].id
    service.bind_card(
        "0011", ids["late_student"], correction=True, expected_student_id=ids["known_student"]
    )
    service.bind_card(
        "0011", ids["known_student"], correction=True, expected_student_id=ids["late_student"]
    )
    payload = {
        "uid": "0011",
        "student_id": ids["late_student"],
        "expected_student_id": ids["known_student"],
        "binding_revision": original_revision,
        "correction": True,
    }
    for name in ("registration.bind", "registration.unbind"):
        with pytest.raises(AttendanceError, match="История привязки"):
            command(backend, name, payload)
    assert service.repository.get_card("0011").student_id == ids["known_student"]


def test_manual_receipt_repeat_after_revoke_never_restores_revoked_evidence(backend):
    backend, ids, _ = backend
    course = ids["courses"][0]
    topic = backend.service.courses.list_topics(course)[0]
    payload = {
        "course_id": course,
        "cells": [{"student_id": ids["known_student"], "topic_id": topic.id}],
        "reason": DEFAULT_REASON,
    }
    result = command(backend, "journal.prepare_mark", payload, "manual-stable")
    assert result["changed"]
    batches = backend.service.redesign.list_manual_batches()
    assert len(batches) == 1
    command(backend, "journal.revoke", {"course_id": course, "batch_id": batches[0].id})
    assert command(backend, "journal.prepare_mark", payload, "manual-stable") == result
    batches = backend.service.redesign.list_manual_batches()
    assert len(batches) == 1 and batches[0].revoked_at is not None
    canonical_json(backend.snapshot(ViewContext(course, ids["lessons"][0], 1)))


def test_backend_close_failure_retains_queue_until_explicit_retry(backend, monkeypatch):
    backend, ids, _ = backend
    course, lesson = ids["courses"][0], ids["lessons"][0]
    session = command(backend, "registration.open", {"course_id": course, "lesson_id": lesson})[
        "session_id"
    ]
    backend.controller._capture("0044", datetime.now(UTC))

    def fail(*args):
        raise OSError("synthetic scan rollback")

    with monkeypatch.context() as patch:
        patch.setattr(backend.service.redesign, "record_session_scan", fail)
        with pytest.raises(PendingScansError):
            command(backend, "registration.close", {"session_id": session}, "close-stable")
    state = backend.snapshot(ViewContext(course, lesson, 1))["registration"]
    assert state["open"] and state["blocked"] and state["closing"] and state["pending_count"] == 1
    command(backend, "registration.retry", {})
    command(backend, "registration.close", {"session_id": session}, "close-stable")
    assert not backend.snapshot(ViewContext(course, lesson, 1))["registration"]["open"]


class WorkerBridge(QObject):
    execute = Signal(object)
    shutdown = Signal()

    def __init__(self):
        super().__init__()
        self.responses = []
        self.received_threads = []
        self.stop_results = []

    @Slot(object)
    def receive(self, response):
        self.responses.append(response)
        self.received_threads.append(QThread.currentThread())

    @Slot(bool, str)
    def stopped(self, success, error):
        self.stop_results.append((success, error))


@pytest.fixture
def worker(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "synthetic-worker.sqlite3"
    ids = seed_workspace(path)
    factory_threads = []
    controls = {"invalid_result": False, "refresh_error": False}

    def factory():
        factory_threads.append(get_ident())
        backend = open_backend(path)
        original_command, original_snapshot = backend.command, backend.snapshot

        def command(operation):
            if controls["invalid_result"]:
                return {"invalid": object()}
            return original_command(operation)

        def snapshot(context):
            if controls["refresh_error"]:
                raise OSError("synthetic projection failure")
            return original_snapshot(context)

        backend.command, backend.snapshot = command, snapshot
        return backend

    bridge, thread = WorkerBridge(), QThread()
    worker = WorkspaceWorker(factory)
    worker.moveToThread(thread)
    thread.started.connect(worker.initialize)
    bridge.execute.connect(worker.execute, Qt.ConnectionType.QueuedConnection)
    bridge.shutdown.connect(worker.shutdown, Qt.ConnectionType.QueuedConnection)
    worker.completed.connect(bridge.receive, Qt.ConnectionType.QueuedConnection)
    worker.stopped.connect(bridge.stopped, Qt.ConnectionType.QueuedConnection)
    thread.finished.connect(worker.deleteLater)
    thread.start()
    try:
        wait_for(lambda: bridge.responses)
        initial = bridge.responses[0]
        assert not initial.error and not initial.refresh_error
        yield bridge, thread, worker, ids, factory_threads, controls
    finally:
        bridge.shutdown.emit()
        wait_for(lambda: bridge.stop_results)
        assert bridge.stop_results[-1] == (True, "")
        thread.quit()
        assert thread.wait(4000)
        assert not isValid(worker)
        thread.deleteLater()
        bridge.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_worker_creates_storage_on_owner_thread_and_emits_immutable_responses(worker):
    bridge, thread, worker, ids, factory_threads, _ = worker
    assert factory_threads[0] != get_ident()
    request = Request(
        Operation.create(
            "worker-open",
            "registration.open",
            {"course_id": ids["courses"][0], "lesson_id": ids["lessons"][0]},
        ),
        ViewContext(ids["courses"][0], ids["lessons"][0], 1),
    )
    bridge.execute.emit(request)
    wait_for(lambda: any(response.request == request for response in bridge.responses))
    response = next(response for response in bridge.responses if response.request == request)
    assert not response.error and not response.refresh_error
    assert json.loads(response.snapshot_json)["registration"]["open"]
    assert json.loads(response.result_json)["session_id"] > 0
    assert all(owner == bridge.thread() for owner in bridge.received_threads)
    assert worker.thread() == thread
    with pytest.raises(FrozenInstanceError):
        response.error = "changed"


@pytest.fixture
def workspace(tmp_path):
    from attendance_lab.presentation.quick.workspace import Workspace

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "synthetic-screen.sqlite3"
    ids = seed_workspace(path)
    ids["path"] = path
    controls = {"open": False, "scan": False, "bind": False, "open_delay": 0.0}

    def factory():
        backend = open_backend(path)
        for name, control in (("open_session", "open"), ("record_session_scan", "scan")):
            original = getattr(backend.service.redesign, name)

            def guarded(*args, _original=original, _control=control, **kwargs):
                if controls[_control]:
                    raise OSError("synthetic controlled " + _control + " failure")
                if _control == "open" and controls["open_delay"]:
                    time.sleep(controls["open_delay"])
                return _original(*args, **kwargs)

            setattr(backend.service.redesign, name, guarded)
        original_bind = backend.service.bind_card

        def bind(*args, **kwargs):
            if controls["bind"]:
                raise OSError("synthetic controlled bind failure")
            return original_bind(*args, **kwargs)

        backend.service.bind_card = bind
        return backend

    model = Workspace(factory)
    try:
        wait_for(lambda: model.ready or model.error)
        assert model.ready, model.error
        yield model, ids, controls
    finally:
        for name in controls:
            controls[name] = False
        if model.canRetry:
            model.retry()
            wait_for(lambda: not model.busy and not model.canRetry)
        if model.worker_thread.isRunning():
            model.requestClose()
            wait_for(lambda: not model.worker_thread.isRunning())
        model.dispose()
        machine, thread = model.machine, model.worker_thread
        model.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(machine) and not isValid(thread)
        app.processEvents()


def select_screen_lesson(model, ids):
    model.selectCourse(ids["courses"][0])
    wait_for(lambda: model.state.get("selected_course_id") == ids["courses"][0] and not model.busy)
    model.selectLesson(ids["lessons"][0])
    wait_for(lambda: model.canOpen)


def test_screen_open_failure_and_retry_preserve_request_identity(workspace):
    model, ids, controls = workspace
    select_screen_lesson(model, ids)
    sent = []
    model.request.connect(sent.append)
    controls["open"] = True
    model.command("registration.open", {})
    wait_for(lambda: model.canRetry)
    assert model.machine.isActive("openFailed")
    assert not model.state["registration"]["open"]
    first = next(request for request in sent if request.operation.kind == "registration.open")
    controls["open"] = False
    model.retry()
    wait_for(lambda: model.machine.isActive("open") and not model.busy)
    retries = [request for request in sent if request.operation.kind == "registration.open"]
    assert len(retries) == 2 and retries[0].operation == retries[1].operation == first.operation
    assert model.state["registration"]["open"]


def test_screen_close_failure_retries_fifo_before_same_close_command(workspace):
    model, ids, controls = workspace
    select_screen_lesson(model, ids)
    model.command("registration.open", {})
    wait_for(lambda: model.machine.isActive("open") and not model.busy)
    session = model.state["registration"]["active_session_id"]
    model.command("system.connect_reader", {"name": DEMO_READER_NAME})
    wait_for(lambda: not model.busy)
    controls["scan"] = True
    model.command("system.simulate", {"uid": "0088"})
    wait_for(lambda: model.state["registration"]["blocked"] and not model.busy)
    sent = []
    model.request.connect(sent.append)
    model.command("registration.close", {})
    wait_for(lambda: model.canRetry)
    assert model.machine.isActive("closeFailed")
    assert model.state["registration"]["active_session_id"] == session
    assert model.state["registration"]["pending_count"] == 1
    assert model.state["registration"]["closing"]
    controls["scan"] = False
    model.retry()
    wait_for(
        lambda: not model.busy and not model.canRetry and not model.state["registration"]["open"]
    )
    close_requests = [r for r in sent if r.operation.kind == "registration.close"]
    assert len(close_requests) == 2
    assert close_requests[0].operation == close_requests[1].operation
    assert any(r.operation.kind == "registration.retry" for r in sent)
    assert model.state["registration"]["pending_count"] == 0


def test_screen_matching_failure_retains_draft_and_can_bind_after_close(workspace):
    model, ids, controls = workspace
    select_screen_lesson(model, ids)
    model.command("registration.open", {})
    wait_for(lambda: model.machine.isActive("open") and not model.busy)
    model.command("system.connect_reader", {"name": DEMO_READER_NAME})
    wait_for(lambda: not model.busy)
    model.command("system.simulate", {"uid": "0099"})
    wait_for(lambda: bool(model.state["registration"]["unknown"]) and not model.busy)
    model.pickCard("0099")
    model.setDraft("matching", "student_id", ids["late_student"])
    wait_for(lambda: model.canSaveMatch)
    model.command("registration.close", {})
    wait_for(lambda: not model.state["registration"]["open"] and not model.busy)
    assert model.drafts["matching"]["uid"] == "0099"
    controls["bind"] = True
    model.command("registration.bind", {})
    # The SCXML macrostep has not run yet, but the captured intent already owns
    # this draft. Later input must not silently become a different unsaved draft.
    assert not model.canEditMatch
    before = dict(model.drafts["matching"])
    model.setDraft("matching", "student_id", ids["known_student"])
    model.setDraft("matching", "query", "late edit")
    model.pickCard("0011", True)
    model.deferMatch()
    assert model.drafts["matching"] == before
    wait_for(lambda: model.canRetry)
    assert not model.canEditMatch
    model.setDraft("matching", "student_id", ids["known_student"])
    assert model.drafts["matching"]["student_id"] == ids["late_student"]
    controls["bind"] = False
    model.retry()
    wait_for(lambda: not model.canRetry and not model.busy and not model.drafts["matching"])
    assert not model.state["registration"]["unknown"]
    assert model.canEditMatch


def test_view_change_during_delayed_mutation_keeps_write_target_and_current_selection(workspace):
    model, ids, controls = workspace
    select_screen_lesson(model, ids)
    controls["open_delay"] = 0.12
    model.command("registration.open", {})
    wait_for(lambda: model.machine.isActive("opening"))
    model.selectCourse(ids["courses"][1])
    wait_for(
        lambda: (
            not model.busy
            and model.machine.isActive("open")
            and model.state.get("selected_course_id") == ids["courses"][1]
        )
    )
    assert model.selectedCourseId == ids["courses"][1]
    assert model.selectedLessonId == 0
    assert model.state["registration"]["active_session_id"] is not None
    assert model.state["registration"]["open"]
    assert model.state["registration"]["logs"] == []
    model.command("registration.close", {})
    wait_for(lambda: not model.busy and not model.state["registration"]["open"])


def test_async_shutdown_refuses_unconfirmed_cards_and_recovers_without_discard(workspace):
    model, ids, controls = workspace
    select_screen_lesson(model, ids)
    model.command("registration.open", {})
    wait_for(lambda: model.machine.isActive("open") and not model.busy)
    controls["scan"] = True
    # Admission alone is thread-safe; let shutdown discover the failure before
    # the periodic worker poll has reported any queue state to the GUI.
    model.worker.backend.controller._capture("00DD", datetime.now(UTC))
    assert not model.state["registration"]["blocked"]
    model.requestClose()
    wait_for(lambda: model.error and model.machine.isActive("running"))
    assert model.worker_thread.isRunning() and not model.shutdownComplete
    assert model.state["registration"]["pending_count"] == 1
    assert model.state["registration"]["blocked"] and model.state["registration"]["closing"]
    controls["scan"] = False
    model.command("registration.retry", {})
    wait_for(lambda: not model.busy and model.state["registration"]["pending_count"] == 0)
    model.requestClose()
    wait_for(lambda: model.shutdownComplete and not model.worker_thread.isRunning())


def test_rapid_distinct_mutations_keep_first_staged_payload_and_reject_second(workspace):
    model, _, _ = workspace
    sent, observed_errors = [], []
    model.request.connect(sent.append)
    model.changed.connect(lambda: observed_errors.append(model.error) if model.error else None)
    model.command("system.discipline", {"name": "Первая принятая команда"})
    model.command("system.discipline", {"name": "Вторая непринятая команда"})
    wait_for(lambda: not model.busy)
    commands = [request for request in sent if request.operation.kind == "system.discipline"]
    assert len(commands) == 1
    assert json.loads(commands[0].operation.payload_json)["name"] == "Первая принятая команда"
    names = {row["name"] for row in model.state["system"]["disciplines"]}
    assert "Первая принятая команда" in names
    assert "Вторая непринятая команда" not in names
    assert any("Дождитесь" in message for message in observed_errors)


def test_worker_serialization_error_returns_response_and_confirmed_write_survives_refresh_error(
    worker,
):
    bridge, _, _, ids, _, controls = worker
    context = ViewContext(ids["courses"][0], ids["lessons"][0], 1)
    invalid = Request(
        Operation.create("invalid-read", "registration.search", {"query": ""}), context
    )
    controls["invalid_result"] = True
    bridge.execute.emit(invalid)
    wait_for(lambda: any(response.request == invalid for response in bridge.responses))
    response = next(response for response in bridge.responses if response.request == invalid)
    assert "JSON" in response.error
    controls["invalid_result"] = False
    controls["refresh_error"] = True
    write = Request(
        Operation.create("confirmed-write", "system.discipline", {"name": "Подтверждённая"}),
        context,
    )
    bridge.execute.emit(write)
    wait_for(lambda: any(response.request == write for response in bridge.responses))
    confirmed = next(response for response in bridge.responses if response.request == write)
    assert not confirmed.error and confirmed.refresh_error
    assert json.loads(confirmed.result_json)["discipline_id"] > 0
    controls["refresh_error"] = False
    refresh = Request(Operation.create("refresh-after-error", "view.refresh", {}), context)
    bridge.execute.emit(refresh)
    wait_for(lambda: any(response.request == refresh for response in bridge.responses))
    refreshed = next(response for response in bridge.responses if response.request == refresh)
    assert "Подтверждённая" in {
        row["name"] for row in json.loads(refreshed.snapshot_json)["system"]["disciplines"]
    }


def test_worker_skips_delayed_old_view_read_but_executes_old_view_mutation(worker):
    bridge, _, _, ids, _, _ = worker
    newer = ViewContext(ids["courses"][1], ids["lessons"][1], 2)
    select_new = Request(Operation.create("select-new", "view.refresh", {}), newer, 1)
    bridge.execute.emit(select_new)
    wait_for(lambda: any(response.request == select_new for response in bridge.responses))
    older = ViewContext(ids["courses"][0], ids["lessons"][0], 1)
    stale = Request(
        Operation.create("stale-search", "registration.search", {"query": "should be skipped"}),
        older,
        1,
    )
    bridge.execute.emit(stale)
    wait_for(lambda: any(response.request == stale for response in bridge.responses))
    skipped = next(response for response in bridge.responses if response.request == stale)
    assert not skipped.error and skipped.snapshot_json == "{}"
    write = Request(
        Operation.create("old-write", "system.discipline", {"name": "Старая цель сохранена"}), older
    )
    bridge.execute.emit(write)
    wait_for(lambda: any(response.request == write for response in bridge.responses))
    acknowledged = next(response for response in bridge.responses if response.request == write)
    assert not acknowledged.error and json.loads(acknowledged.result_json)["discipline_id"] > 0
    refresh = Request(Operation.create("new-refresh", "view.refresh", {}), newer, 2)
    bridge.execute.emit(refresh)
    wait_for(lambda: any(response.request == refresh for response in bridge.responses))
    state = json.loads(
        next(response for response in bridge.responses if response.request == refresh).snapshot_json
    )
    assert state["registration"]["query"] == ""
    assert state["selected_course_id"] == ids["courses"][1]
    assert "Старая цель сохранена" in {row["name"] for row in state["system"]["disciplines"]}


def test_periodic_poll_keeps_committed_public_confirmation_when_refresh_fails(worker):
    bridge, _, owner, ids, _, controls = worker
    context = ViewContext(ids["courses"][0], ids["lessons"][0], 1)
    opening = Request(
        Operation.create(
            "poll-open",
            "registration.open",
            {"course_id": context.course_id, "lesson_id": context.lesson_id},
        ),
        context,
    )
    bridge.execute.emit(opening)
    wait_for(lambda: any(response.request == opening for response in bridge.responses))
    controls["refresh_error"] = True
    # This is the reader's admission-only API; it never calls storage on this thread.
    owner.backend.controller._capture("0011", datetime.now(UTC))
    wait_for(
        lambda: any(
            response.refresh_error
            and any(result.kind == "success" for result in response.public_results)
            for response in bridge.responses
        )
    )
    confirmation = next(
        response
        for response in bridge.responses
        if response.refresh_error
        and any(result.kind == "success" for result in response.public_results)
    )
    assert confirmation.request is None and not confirmation.error
    assert next(
        result for result in confirmation.public_results if result.kind == "success"
    ).full_name
    controls["refresh_error"] = False


def test_shutdown_drains_accepted_staged_mutation_before_deleting_storage_owner(workspace):
    from attendance_lab.infrastructure.sqlite import SQLiteRepository

    model, ids, _ = workspace
    model.command("system.discipline", {"name": "Принято перед закрытием"})
    model.requestClose()
    wait_for(lambda: model.shutdownComplete and not model.worker_thread.isRunning())
    repository = SQLiteRepository(ids["path"], mode="demo")
    try:
        assert "Принято перед закрытием" in {
            discipline.name for discipline in repository.list_disciplines()
        }
    finally:
        repository.close()


def test_shutdown_ready_signal_observes_terminal_chart_before_windows_can_close(workspace):
    model, _, _ = workspace
    observed = []
    model.shutdownReady.connect(
        lambda: observed.append((model.shutdownComplete, model.worker_thread.isRunning()))
    )
    model.requestClose()
    wait_for(lambda: bool(observed))
    assert observed == [(True, False)]


@pytest.mark.parametrize("kind, frozen", [("lecture", False), ("lecture", True), ("lab", True)])
def test_registration_expected_rows_use_stream_or_assigned_subgroup_without_repairing_history(
    backend, kind, frozen
):
    from dataclasses import replace
    from datetime import date

    from attendance_lab.application.course_absences import expected_student_ids

    backend, ids, _ = backend
    service = backend.service
    headers = ["№", "ФИО", "Специальность", "Профилизация", "Курс", "Группа", "Номер зачётки"]
    rows = [
        ["1", "Учебный Студент", "Учебная специальность", "", "2", "1", "0001"],
        ["2", "Второй В Подгруппе", "Учебная специальность", "", "2", "1", "0002"],
        ["3", "Другой В Потоке", "Учебная специальность", "", "2", "2", "0003"],
    ]
    service.redesign.apply_import(service.redesign.preview_import(headers, rows, 2026))
    groups = sorted(service.redesign.list_groups(), key=lambda group: group.number)
    group = groups[0]
    course = service.courses.save_course(
        ids["discipline"], group.academic_year_id, 1, 2, kind, 1, "Проверка состава"
    )
    roster = service.courses.add_groups(
        course.id,
        {group.id},
        "Назначенная линия",
        {ids["known_student"]} if kind == "lab" else None,
    )
    service.courses.save_topic(course.id, 1, "Состав без ремонта истории")
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 17), 3)
    block = service.redesign.get_block(lesson.block_id)
    if frozen:
        with service.repository.transaction():
            for pair_id in block.pair_ids:
                service.repository.put_lesson(
                    replace(service.get_lesson(pair_id), roster_fixed=True, state="completed")
                )
                service.repository.put_lesson_roster(pair_id, set())
    before = service.repository.rd_revision()
    snapshot = backend.registration.snapshot(course.id, lesson.id)
    assert {row["id"] for row in snapshot["rows"]} == expected_student_ids(
        service, course.id, block.id
    )
    assert all(not row["outside"] and not row["present"] for row in snapshot["rows"])
    if kind == "lecture":
        assert len(snapshot["rows"]) == 3  # Whole study-course stream, beyond the assigned group.
    else:
        assert {row["id"] for row in snapshot["rows"]} == {ids["known_student"]}
    assert service.repository.rd_revision() == before
    if frozen:
        assert all(service.repository.get_lesson_roster(pair) == set() for pair in block.pair_ids)
    canonical_json(snapshot)
