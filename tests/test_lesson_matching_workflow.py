"""The normal Create lesson path must support card-to-name matching on the home page."""

from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DEMO_READER_NAME, DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.admin import MainWindow


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = AttendanceService(SQLiteRepository(tmp_path / "synthetic.sqlite3", mode="demo"))
    service.redesign.apply_import(
        service.redesign.preview_import(
            [
                "№",
                "ФИО",
                "Специальность",
                "Специализация/профилизация",
                "Курс",
                "Группа",
                "№ зачётки",
            ],
            [["1", "Семёнов Иван", "Физика", "Учебный профиль", "2", "10", "000123"]],
            datetime.now().year,
        )
    )
    discipline = service.save_discipline("Java")
    student = service.list_students()[0]
    service.set_discipline_roster(discipline.id, {student.id})
    controller = AttendanceController(service, DemoReader)
    widget = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "synthetic.sqlite3",
        default_timezone="Europe/Minsk",
    )
    widget.event_timer.stop()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    service.close()
    app.processEvents()


def test_create_lesson_dialog_adds_selectable_home_entry_and_opens_registration(window):
    def fill_dialog():
        dialog = QApplication.activeModalWidget()
        dialog.title_edit.setText("Лабораторная работа 4")
        dialog.accept()

    QTimer.singleShot(0, fill_dialog)
    window.schedule.edit_lesson(False)
    lesson = window.service.list_lessons()[0]
    selector = window.workspace.blocks
    index = next((i for i in range(selector.count()) if lesson.title in selector.itemText(i)), -1)
    assert index >= 0, "Занятие из «Создать занятие» отсутствует на главной странице"
    selector.setCurrentIndex(index)
    window.workspace.open_selected()
    assert window.controller.active_lesson_id == lesson.id
    assert window.controller.active_session_id is None


def test_standalone_scan_keeps_draft_and_binds_selected_name_without_another_tap(window):
    now = datetime.now(UTC)
    discipline = window.service.list_disciplines()[0]
    lesson = window.service.save_lesson(
        discipline.id,
        now - timedelta(minutes=1),
        now + timedelta(minutes=80),
        "Europe/Minsk",
        title="Лабораторная работа 4",
    )
    window.refresh()
    window.controller.connect_reader(DEMO_READER_NAME)
    window.controller.open_lesson(lesson.id)
    window.process_events()
    workspace = window.workspace
    workspace.course.setCurrentIndex(workspace.course.findData(2))
    workspace.search.setText("семенов")
    workspace.search.setCursorPosition(2)
    workspace.search.setFocus()
    workspace.matches.setCurrentRow(0)
    selected = workspace.matches.currentItem().data(Qt.ItemDataRole.UserRole)
    window.controller.simulate("0011")
    window.process_events()
    assert workspace.pinned_uid == "0011"
    assert workspace.search.text() == "семенов"
    assert workspace.search.cursorPosition() == 2
    assert QApplication.focusWidget() is workspace.search
    assert workspace.matches.currentItem().data(Qt.ItemDataRole.UserRole) == selected
    QTest.keyClick(workspace.search, Qt.Key.Key_Down)
    QTest.keyClick(workspace.matches, Qt.Key.Key_Return)
    assert window.service.lesson_log(lesson.id)[0].student_id == selected
    assert workspace.pinned_uid is None
    assert workspace.search.text() == ""
    assert window.service.report(discipline.id).rows[0].cells[0].state == "present"


def test_only_today_filter_can_reveal_standalone_lessons_on_other_dates(window):
    start = datetime.now(UTC) + timedelta(days=1)
    window.service.save_lesson(
        window.service.list_disciplines()[0].id,
        start,
        start + timedelta(minutes=80),
        "UTC",
        title="Завтрашнее занятие",
    )
    window.refresh()
    assert window.workspace.blocks.count() == 0
    window.workspace.today.setChecked(False)
    assert window.workspace.blocks.count() == 1
    assert "Завтрашнее занятие" in window.workspace.blocks.itemText(0)


@pytest.mark.parametrize("operation", ["quick", "selected"])
def test_manual_actions_also_work_for_the_current_standalone_lesson(window, monkeypatch, operation):
    now = datetime.now(UTC)
    discipline = window.service.list_disciplines()[0]
    lesson = window.service.save_lesson(
        discipline.id,
        now - timedelta(minutes=1),
        now + timedelta(minutes=80),
        "Europe/Minsk",
        title="Лабораторная работа 4",
    )
    window.refresh()
    window.controller.open_lesson(lesson.id)
    window.process_events()
    workspace = window.workspace
    student = window.service.list_students()[0]
    close_error = QTimer()
    close_error.timeout.connect(
        lambda: (
            QApplication.activeModalWidget().accept() if QApplication.activeModalWidget() else None
        )
    )
    close_error.start(10)
    try:
        if operation == "quick":
            workspace.start_manual()
            assert workspace.manual_mode
            workspace.search.setText("семенов")
            workspace.matches.setCurrentRow(0)
            workspace.confirm_match()
        else:
            monkeypatch.setattr(
                QInputDialog, "getText", lambda *args: ("Пропуск не работает", True)
            )
            workspace.table.selectRow(0)
            workspace.manual_block()
        cell = window.service.report(discipline.id).rows[0].cells[0]
        assert cell.state == "present" and cell.manual_confirmation
        assert window.service.redesign.evidence(student.id, lesson.id)[0].kind == "manual"
    finally:
        close_error.stop()


@pytest.mark.parametrize("transition", ["close", "switch"])
def test_manual_draft_cannot_silently_credit_a_previous_registration(window, transition):
    now = datetime.now(UTC)
    discipline = window.service.list_disciplines()[0]
    old = window.service.save_lesson(
        discipline.id,
        now - timedelta(hours=3),
        now - timedelta(hours=2),
        "UTC",
        title="Прошлая пара",
    )
    current = window.service.save_lesson(
        discipline.id,
        now - timedelta(minutes=1),
        now + timedelta(minutes=80),
        "UTC",
        title="Текущая пара",
    )
    window.refresh()
    window.controller.open_lesson(old.id)
    window.process_events()
    workspace = window.workspace
    workspace.start_manual()
    if transition == "close":
        window.controller.close_lesson()
    else:
        window.controller.open_lesson(current.id)
    window.process_events()
    workspace.search.setText("семенов")
    workspace.matches.setCurrentRow(0)
    dismiss = QTimer()
    dismiss.timeout.connect(
        lambda: (
            QApplication.activeModalWidget().accept() if QApplication.activeModalWidget() else None
        )
    )
    dismiss.start(10)
    try:
        workspace.confirm_match()
        student = window.service.list_students()[0]
        assert window.service.redesign.evidence(student.id, old.id) == []
        assert window.service.redesign.evidence(student.id, current.id) == []
    finally:
        dismiss.stop()


@pytest.mark.parametrize("operation", ["remove", "replace", "cancel"])
def test_last_bound_card_can_be_corrected_from_home_without_losing_other_card_draft(
    window, operation
):
    service = window.service
    service.redesign.apply_import(
        service.redesign.preview_import(
            [
                "№",
                "ФИО",
                "Специальность",
                "Специализация/профилизация",
                "Курс",
                "Группа",
                "№ зачётки",
            ],
            [["2", "Борисов Борис", "Физика", "Учебный профиль", "2", "10", "000124"]],
            datetime.now().year,
        )
    )
    now = datetime.now(UTC)
    lesson = service.save_lesson(
        service.list_disciplines()[0].id,
        now - timedelta(minutes=1),
        now + timedelta(minutes=80),
        "UTC",
    )
    window.refresh()
    window.controller.open_lesson(lesson.id)
    window.controller.connect_reader(DEMO_READER_NAME)
    window.process_events()
    window.controller.simulate("0011")
    window.controller.simulate("0022")
    window.process_events()
    workspace = window.workspace
    workspace.search.setText("семенов")
    workspace.matches.setCurrentRow(0)
    workspace.confirm_match()
    before = service.lesson_log(lesson.id)[0]
    original_owner = before.student_id
    assert original_owner is not None
    assert workspace.pinned_uid == "0022"
    workspace.search.setText("семен")
    workspace.search.setCursorPosition(2)
    errors = []
    edited = []

    def edit_dialog():
        dialog = QApplication.activeModalWidget()
        try:
            edited.append(True)
            assert dialog.card.uid == "0011"
            dialog.activateWindow()
            QApplication.processEvents()
            if operation in ("remove", "cancel"):
                dialog.remove_binding.setChecked(True)
                dialog.reject() if operation == "cancel" else dialog.accept()
            else:
                dialog.search.setText("борисов")
                QTest.keyClick(dialog.search, Qt.Key.Key_Down)
                QTest.keyClick(dialog.list_widget, Qt.Key.Key_Return)
                assert dialog.result() == 1
        except Exception as exc:
            errors.append(exc)
            if dialog:
                dialog.reject()

    QTimer.singleShot(0, edit_dialog)
    QTest.keyClick(window, Qt.Key.Key_F4)
    assert edited, "F4 не открыл исправление карты"
    assert not errors, errors
    after = service.lesson_log(lesson.id)[0]
    expected_owner = (
        None
        if operation == "remove"
        else next(s.id for s in service.list_students() if s.full_name == "Борисов Борис")
        if operation == "replace"
        else original_owner
    )
    assert after.student_id == expected_owner
    assert (after.uid, after.first_seen, after.last_seen) == (
        before.uid,
        before.first_seen,
        before.last_seen,
    )
    assert workspace.pinned_uid == "0022"
    assert workspace.search.text() == "семен"
    assert workspace.search.cursorPosition() == 2
    assert ("0011" in workspace.pending_uids) is (operation == "remove")


def test_switching_queued_card_requires_a_new_explicit_student_selection(window):
    workspace = window.workspace
    workspace.pending_uids = ["0011", "0022"]
    workspace.queue.addItems(workspace.pending_uids)
    workspace.pinned_uid = "0011"
    workspace.course.setCurrentIndex(workspace.course.findData(2))
    workspace.search.setText("семенов")
    workspace.matches.setCurrentRow(0)
    workspace.queue.setCurrentRow(1)
    assert workspace.pinned_uid == "0022"
    assert workspace.search.text() == "семенов"
    assert workspace.matches.currentItem() is None


def test_card_directory_search_cannot_leave_another_card_selected(window):
    service = window.service
    owner = service.list_students()[0]
    service.bind_card("0011", owner.id)
    service.bind_card("0022", owner.id)
    pane = window.students
    pane.refresh()
    pane.cards.selectRow(1)
    assert pane.card_action.isEnabled()
    pane.search.setText("0011")
    assert pane.cards.rowCount() == 1
    assert pane.cards.item(0, 0).text() == "0011"
    assert not pane.card_action.isEnabled()
    pane.cards.selectRow(0)
    pane.search.setText("Семёнов")
    assert pane.cards.rowCount() == 2
    pane.unknown_only.setChecked(True)
    assert pane.cards.rowCount() == 0
    assert not pane.card_action.isEnabled()


def test_keyboard_search_and_results_are_visible_after_scrolling_small_window(window):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QScrollArea

    workspace = window.workspace
    window.resize(980, 720)
    QApplication.processEvents()
    scroll = next(
        s for s in workspace.findChildren(QScrollArea) if s.widget().isAncestorOf(workspace.search)
    )
    workspace.course.setCurrentIndex(workspace.course.findData(2))
    workspace.search.setText("семенов")
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    workspace.table.setFocus()
    window.commands.search()
    top = workspace.search.mapTo(scroll.viewport(), QPoint(0, 0)).y()
    assert 0 <= top <= scroll.viewport().height() - workspace.search.height()
    QTest.keyClick(workspace.search, Qt.Key.Key_Down)
    item = workspace.matches.currentItem()
    row = workspace.matches.visualItemRect(item)
    top = workspace.matches.viewport().mapTo(scroll.viewport(), row.topLeft()).y()
    assert 0 <= top <= scroll.viewport().height() - row.height()
