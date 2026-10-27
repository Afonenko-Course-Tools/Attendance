"""Synthetic private-window regressions for the most recent physical card tap."""

# The imported pytest fixture intentionally shares its name with fixture arguments.
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_lesson_matching_workflow import window  # noqa: F401

from attendance_lab.infrastructure.readers import DEMO_READER_NAME


@pytest.fixture
def registration(window):
    now = datetime.now(UTC)
    lesson = window.service.save_lesson(
        window.service.list_disciplines()[0].id,
        now - timedelta(minutes=1),
        now + timedelta(minutes=80),
        "UTC",
        title="Синтетическая проверка последней карты",
    )
    window.refresh()
    window.controller.open_lesson(lesson.id)
    window.controller.connect_reader(DEMO_READER_NAME)
    window.process_events()
    return window, lesson, window.service.list_students()[0]


def tap(window, uid):
    window.controller.simulate(uid)
    window.process_events()
    QApplication.processEvents()


def assert_visible(window, widget):
    assert widget.isVisible(), "Последнее предъявление должно быть видно оператору"
    parent = widget.parentWidget()
    while parent is not None:
        point = widget.mapTo(parent, QPoint(0, 0))
        assert point.x() >= 0 and point.y() >= 0
        assert point.x() + widget.width() <= parent.width()
        assert point.y() + widget.height() <= parent.height()
        if parent is window:
            break
        parent = parent.parentWidget()


def select_student(workspace):
    workspace.course.setCurrentIndex(workspace.course.findData(2))
    workspace.search.setText("семенов")
    workspace.matches.setCurrentRow(0)
    assert workspace.matches.currentItem() is not None


@pytest.mark.parametrize("size", [(980, 720), (1366, 768)])
@pytest.mark.parametrize("repeat", [False, True])
def test_bound_tap_visible_with_name_binding_and_saved_time(registration, size, repeat):
    window, lesson, student = registration
    window.resize(*size)
    QApplication.processEvents()
    assert (window.width(), window.height()) == size
    window.service.bind_card("A001", student.id)
    tap(window, "A001")
    first = window.service.lesson_log(lesson.id)[0]
    if repeat:
        tap(window, "A001")
    scan = window.service.lesson_log(lesson.id)[0]
    assert scan.student_id == student.id
    assert scan.first_seen == first.first_seen and scan.last_seen >= first.last_seen
    label = window.workspace.last_card_label
    assert student.full_name in label.text()
    assert "привязана" in label.text().lower()
    assert "не привязана" not in label.text().lower()
    assert_visible(window, label)


def test_unknown_tap_preserves_search_cursor_focus_selection_and_saved_scan(registration):
    window, lesson, _ = registration
    workspace = window.workspace
    select_student(workspace)
    selected = workspace.matches.currentItem().data(Qt.ItemDataRole.UserRole)
    workspace.search.setCursorPosition(2)
    workspace.search.setFocus()
    QApplication.processEvents()
    tap(window, "A101")
    assert workspace.search.text() == "семенов"
    assert workspace.search.cursorPosition() == 2
    assert QApplication.focusWidget() is workspace.search
    assert workspace.matches.currentItem().data(Qt.ItemDataRole.UserRole) == selected
    saved = window.service.lesson_log(lesson.id)
    assert len(saved) == 1 and saved[0].uid == "A101" and saved[0].student_id is None
    assert saved[0].first_seen is not None and saved[0].last_seen >= saved[0].first_seen
    assert "не привязана" in workspace.last_card_label.text().lower()
    assert_visible(window, workspace.last_card_label)


def test_second_unknown_is_last_while_first_card_stays_pinned(registration):
    window, lesson, _ = registration
    workspace = window.workspace
    tap(window, "A101")
    tap(window, "A202")
    assert workspace.pinned_uid == "A101" and "A101" in workspace.uid_label.text()
    text = workspace.last_card_label.text()
    assert "A202" in text and "не привязана" in text.lower()
    assert "очеред" in text.lower()
    assert {scan.uid for scan in window.service.lesson_log(lesson.id)} == {"A101", "A202"}
    assert_visible(window, workspace.last_card_label)


def test_bound_tap_updates_feedback_while_unknown_card_stays_pinned(registration):
    window, lesson, student = registration
    workspace = window.workspace
    window.service.bind_card("A001", student.id)
    tap(window, "A101")
    select_student(workspace)
    workspace.search.setCursorPosition(3)
    workspace.search.setFocus()
    QApplication.processEvents()
    tap(window, "A001")
    assert workspace.pinned_uid == "A101"
    assert "A101" in workspace.uid_label.text()
    assert student.full_name in workspace.last_card_label.text()
    assert "привязана" in workspace.last_card_label.text().lower()
    assert "не привязана" not in workspace.last_card_label.text().lower()
    assert workspace.search.text() == "семенов" and workspace.search.cursorPosition() == 3
    assert QApplication.focusWidget() is workspace.search
    assert (
        next(s for s in window.service.lesson_log(lesson.id) if s.uid == "A001").student_id
        == student.id
    )
    assert_visible(window, workspace.last_card_label)


@pytest.mark.parametrize("bind_latest", [False, True])
def test_binding_preserves_last_physical_tap_and_recorded_times(registration, bind_latest):
    window, lesson, student = registration
    workspace = window.workspace
    tap(window, "A101")
    tap(window, "A202")
    before = {scan.uid: scan for scan in window.service.lesson_log(lesson.id)}
    if bind_latest:
        workspace.queue.setCurrentRow(1)
    select_student(workspace)
    QTest.keyClick(workspace.matches, Qt.Key.Key_Return)
    bound_uid = "A202" if bind_latest else "A101"
    after = {scan.uid: scan for scan in window.service.lesson_log(lesson.id)}
    assert after[bound_uid].student_id == student.id
    assert (after[bound_uid].first_seen, after[bound_uid].last_seen) == (
        before[bound_uid].first_seen,
        before[bound_uid].last_seen,
    )
    text = workspace.last_card_label.text()
    assert "A202" in text, "Привязка старой карты не является новым предъявлением"
    if bind_latest:
        assert student.full_name in text and "привязана" in text.lower()
        assert "не привязана" not in text.lower()
    else:
        assert student.full_name not in text and "не привязана" in text.lower()
    assert workspace.pinned_uid == ("A101" if bind_latest else "A202")
    assert_visible(window, workspace.last_card_label)


@pytest.mark.parametrize("transition", ["close", "switch"])
def test_registration_transition_clears_previous_student(registration, transition):
    window, _, student = registration
    window.service.bind_card("A001", student.id)
    tap(window, "A001")
    assert student.full_name in window.workspace.last_card_label.text()
    if transition == "close":
        window.controller.close_lesson()
    else:
        now = datetime.now(UTC)
        other = window.service.save_lesson(
            window.service.list_disciplines()[0].id,
            now,
            now + timedelta(minutes=80),
            "UTC",
            title="Другая синтетическая пара",
        )
        window.controller.open_lesson(other.id)
    window.process_events()
    QApplication.processEvents()
    assert student.full_name not in window.workspace.last_card_label.text()
    assert_visible(window, window.workspace.last_card_label)


def test_failed_scan_replaces_previous_success_with_explicit_error(registration, monkeypatch):
    window, lesson, student = registration
    window.service.bind_card("A001", student.id)
    tap(window, "A001")
    before = window.service.lesson_log(lesson.id)

    def fail_save(*args, **kwargs):
        raise OSError("Synthetic save failure")

    monkeypatch.setattr(window.service, "record_scan", fail_save)
    tap(window, "A303")
    text = window.workspace.last_card_label.text().lower()
    assert "ошиб" in text or "не удалось" in text
    assert student.full_name.lower() not in text
    assert window.service.lesson_log(lesson.id) == before
    assert_visible(window, window.workspace.last_card_label)


def test_deferred_last_card_is_not_described_as_still_in_queue(registration):
    window, _, _ = registration
    tap(window, "A101")
    window.workspace.defer()
    assert "отлож" in window.workspace.last_card_label.text().lower()
    assert "ожидает в очереди" not in window.workspace.last_card_label.text().lower()


def test_binding_older_card_identifies_f4_as_last_binding_not_last_tap(registration):
    window, _, student = registration
    tap(window, "A101")
    tap(window, "A202")
    select_student(window.workspace)
    window.workspace.confirm_match()
    assert "A202" in window.workspace.last_card_label.text()
    assert student.full_name in window.workspace.correct_button.text()
    assert student.full_name in window.workspace.correct_button.toolTip()


def test_old_queued_failure_does_not_show_as_error_for_new_registration(registration, monkeypatch):
    window, _, _ = registration
    now = datetime.now(UTC)
    other = window.service.save_lesson(
        window.service.list_disciplines()[0].id,
        now,
        now + timedelta(minutes=80),
        "UTC",
        title="Следующая синтетическая пара",
    )
    window.controller.simulate("A303")
    monkeypatch.setattr(
        window.service, "record_scan", lambda *args: (_ for _ in ()).throw(OSError("synthetic"))
    )
    window.controller.open_lesson(other.id)
    window.process_events()
    assert "ошиб" not in window.workspace.last_card_label.text().lower()
    assert "не удалось" not in window.workspace.last_card_label.text().lower()


def test_display_failure_does_not_claim_a_saved_mark_failed(registration, monkeypatch):
    window, lesson, student = registration
    window.service.bind_card("A001", student.id)

    def fail_update(*args):
        raise RuntimeError("Synthetic screen update failure")

    monkeypatch.setattr(window.schedule, "update_counts", fail_update)
    tap(window, "A001")
    assert window.service.lesson_log(lesson.id)[0].student_id == student.id
    assert "ошиб" in window.workspace.last_card_label.text().lower()
    assert "не удалось сохранить" not in window.workspace.last_card_label.text().lower()
    assert "не удалось сохранить" not in window.public_window.headline.text().lower()


def test_focused_search_stays_visible_after_window_shrinks(registration):
    window, _, _ = registration
    workspace = window.workspace
    window.resize(1366, 768)
    QApplication.processEvents()
    workspace.match_scroll.verticalScrollBar().setValue(0)
    window.activateWindow()
    workspace.table.setFocus()
    workspace.search.setFocus()
    QApplication.processEvents()
    window.resize(980, 720)
    QTest.qWait(50)
    viewport = workspace.match_scroll.viewport()
    rect = workspace.search.rect()
    rect.moveTopLeft(workspace.search.mapTo(viewport, QPoint(0, 0)))
    assert QApplication.focusWidget() is workspace.search
    assert viewport.rect().contains(rect)
