"""Offscreen view tests exercise public privacy and real service integration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import PublicResult
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DEMO_READER_NAME, DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.admin import MainWindow
from attendance_lab.presentation.public import PublicWindow


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def window(app, tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "demo.sqlite3", mode="demo"))
    controller = AttendanceController(service, DemoReader)
    widget = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "demo.sqlite3",
        default_timezone="UTC",
    )
    widget.event_timer.stop()
    yield widget
    widget.close()
    service.close()
    app.processEvents()


def _all_public_text(window: PublicWindow) -> str:
    return "\n".join(label.text() for label in window.findChildren(QLabel))


def test_public_uses_only_allowlisted_messages_and_plain_names(app):
    window = PublicWindow()
    window.set_lesson("Физика · 07.09.2026 10:00 UTC", registration_open=True)
    window.display(
        PublicResult("success", "secret UID=00112233", "<b>Иванов</b>", datetime.now(UTC))
    )
    assert "00112233" not in _all_public_text(window)
    assert window.person.text() == "<b>Иванов</b>"
    assert window.person.textFormat() == Qt.TextFormat.PlainText
    window.display(
        PublicResult("error", "UID 00112233 /private/db.sqlite3 traceback", "private name")
    )
    text = _all_public_text(window)
    assert "Не удалось сохранить отметку" in text
    assert "00112233" not in text and "sqlite3" not in text and "private name" not in text
    window.close()


def test_public_timeout_and_stop_hide_previous_identity(app):
    window = PublicWindow()
    window.set_lesson("Занятие", registration_open=True)
    assert window._timeout_ms == 3000
    window.set_timeout_seconds(1)
    window.display(PublicResult("success", "", "Синтетический студент"))
    loop = QEventLoop()
    QTimer.singleShot(1150, loop.quit)
    loop.exec()
    assert window.person.text() == ""
    assert window.headline.text() == "Приложите карту"
    window.display(PublicResult("repeat", "", "Синтетический студент"))
    window.set_lesson("", registration_open=False)
    assert window.person.text() == ""
    assert window.headline.text() == "Регистрация закрыта"
    assert not window.timer.isActive()
    window.close()


def test_public_feedback_colors_reset_with_identity(app):
    window = PublicWindow()
    window.set_lesson("Занятие", registration_open=True)
    window.display(PublicResult("success", "", "Студент"))
    assert "#86efac" in window.headline.styleSheet()
    assert "#86efac" in window.person.styleSheet()
    window.display(PublicResult("repeat", "", "Студент"))
    assert "#fde047" in window.headline.styleSheet()
    assert "#fde047" in window.person.styleSheet()
    window.show_idle()
    assert "#ffffff" in window.headline.styleSheet()
    assert window.person.text() == ""
    window.close()


def test_zero_roster_two_cards_and_attempt_colors_in_each_lesson(window):
    now = datetime.now(UTC)
    discipline = window.service.save_discipline("Java")
    lessons = [
        window.service.save_lesson(
            discipline.id, now + timedelta(hours=i), now + timedelta(hours=i + 1), "UTC"
        )
        for i in range(2)
    ]
    window.controller.connect_reader(DEMO_READER_NAME)
    window.controller.open_lesson(lessons[0].id)
    window.log_lesson_id = lessons[0].id
    window.process_events()
    window.controller.simulate("00112233445566")
    window.process_events()
    assert window.logs_window.log_table.item(0, 0).background().color().name() == "#dcfce7"
    window.logs_window.show()
    log_table = window.logs_window.log_table
    log_table.selectRow(0)
    QApplication.processEvents()
    rect = log_table.visualItemRect(log_table.item(0, 0))
    rendered = log_table.viewport().grab().toImage()
    assert rendered.pixelColor(rect.right() - 6, rect.bottom() - 5).name() == "#dcfce7"
    window.controller.simulate("00112233445566")
    window.process_events()
    assert window.logs_window.log_table.rowCount() == 1
    assert window.logs_window.log_table.item(0, 0).background().color().name() == "#fef3c7"
    QApplication.processEvents()
    rendered = log_table.viewport().grab().toImage()
    assert rendered.pixelColor(rect.right() - 6, rect.bottom() - 5).name() == "#fef3c7"
    assert "Повторная" in window.logs_window.feedback.text()
    window.controller.simulate("00AABBCC")
    window.process_events()
    assert window.logs_window.log_table.rowCount() == 2
    assert "0 чел." in window.schedule.lessons.item(0, 5).text()
    assert window.schedule.lessons.horizontalHeaderItem(5).text() == "Ожидаемый состав"
    assert (
        window.schedule.lessons.item(0, 6).text() == "Карт: 2 · Студентов: 0\nБез сопоставления: 2"
    )
    assert "Уникальных карт: 2" in window.logs_window.counts.text()

    student = window.service.save_student("Иванов Иван", student_id="S-1")
    for uid in ("00112233445566", "00AABBCC"):
        window.service.bind_card(uid, student.id)
    window.refresh()
    assert "Карт: 2 · Студентов: 1" in window.schedule.lessons.item(0, 6).text()
    assert "Распознано студентов: 1" in window.logs_window.counts.text()
    assert window.service.get_lesson_roster(lessons[0].id) == set()

    window.controller.open_lesson(lessons[1].id)
    window.log_lesson_id = lessons[1].id
    window.process_events()
    assert window.logs_window.feedback.isHidden()
    window.controller.simulate("00112233445566")
    window.process_events()
    assert "#86efac" in window.public_window.headline.styleSheet()
    assert window.logs_window.log_table.item(0, 0).background().color().name() == "#dcfce7"
    # Another card belonging to a student already present is also a repeat.
    window.controller.simulate("00AABBCC")
    window.process_events()
    assert "#fde047" in window.public_window.headline.styleSheet()
    assert "Повторная" in window.logs_window.feedback.text()
    assert window.logs_window.log_table.rowCount() == 2


def test_unknown_scan_late_binding_unique_log_and_browsing_context(window):
    now = datetime.now(UTC)
    discipline = window.service.save_discipline("Электродинамика")
    other = window.service.save_discipline("Численные методы")
    lesson = window.service.save_lesson(
        discipline.id, now - timedelta(minutes=10), now + timedelta(minutes=70), "UTC", "Семинар"
    )
    window.refresh()
    assert window.readers.currentData() is None  # Never silently select first interface.
    window.readers.setCurrentIndex(window.readers.findData(DEMO_READER_NAME))
    window.connect_reader()
    window.open_registration(lesson.id)
    window.demo_uid.setText("00 11 22 33")
    window.simulate()
    first = window.service.lesson_log(lesson.id)[0].first_seen
    assert window.public_window.headline.text() == "Отметка сохранена"
    assert "00112233" not in _all_public_text(window.public_window)
    assert window.logs_window.log_table.rowCount() == 1
    assert window.logs_window.log_table.item(0, 2).text() == "00112233"
    student = window.service.save_student("Тестовый Студент", "Ф-1", "S-1")
    window.service.bind_card("00112233", student.id)
    window.service.set_lesson_roster(lesson.id, {student.id})
    window.refresh()
    window.reports.discipline.setCurrentIndex(window.reports.discipline.findData(discipline.id))
    assert window.reports.matrix.rowCount() == 1
    assert "Присутствовал" in window.reports.matrix.item(0, 3).text()
    window.simulate()
    assert window.public_window.headline.text() == "Отметка уже существует"
    assert window.public_window.person.text() == student.full_name
    assert window.logs_window.log_table.rowCount() == 1
    assert window.service.lesson_log(lesson.id)[0].first_seen == first
    window.schedule.discipline.setCurrentIndex(window.schedule.discipline.findData(other.id))
    assert window.controller.active_lesson_id == lesson.id
    assert "Электродинамика" in window.active_banner.text()
    window.close_registration()
    assert window.public_window.person.text() == ""
    window.demo_uid.setText("00445566")
    window.simulate()
    assert len(window.service.lesson_log(lesson.id)) == 1


def test_failed_commit_does_not_display_success_or_raw_details(window, monkeypatch):
    now = datetime.now(UTC)
    discipline = window.service.save_discipline("Тест")
    lesson = window.service.save_lesson(
        discipline.id, now - timedelta(minutes=1), now + timedelta(minutes=5), "UTC"
    )
    window.controller.connect_reader(DEMO_READER_NAME)
    window.controller.open_lesson(lesson.id)
    window.process_events()

    def fail(*args, **kwargs):
        raise OSError("00112233 /private/attendance.sqlite3 disk full")

    monkeypatch.setattr(window.service, "record_scan", fail)
    window.controller.simulate("00112233")
    window.process_events()
    assert window.service.lesson_log(lesson.id) == []
    text = _all_public_text(window.public_window)
    assert "Не удалось сохранить отметку" in text
    assert "00112233" not in text and "disk full" not in text
    assert "disk full" in window.device_status.text()


def test_real_mode_has_no_simulator_field(app, tmp_path: Path):
    service = AttendanceService(SQLiteRepository(tmp_path / "real.sqlite3", mode="real"))
    controller = AttendanceController(service, DemoReader)
    window = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=False,
        database_path=tmp_path / "real.sqlite3",
        default_timezone="UTC",
    )
    assert not hasattr(window, "demo_uid")
    window.close()
    service.close()


def test_roster_assignment_applies_only_explicitly_selected_lessons(window, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QDialog

    from attendance_lab.presentation import schedule
    from attendance_lab.presentation.dialogs import StudentSelectionDialog

    now = datetime.now(UTC)
    discipline = window.service.save_discipline("Выбор состава")
    student = window.service.save_student("Участник состава", "Т-1", "ROSTER-1")
    lessons = [
        window.service.save_lesson(
            discipline.id,
            now + timedelta(days=index),
            now + timedelta(days=index, hours=1),
            "UTC",
        )
        for index in range(3)
    ]
    window.refresh()
    selection = window.schedule.lessons.selectionModel()
    for row in (0, 2):
        selection.select(
            window.schedule.lessons.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )

    class AcceptedRoster(StudentSelectionDialog):
        def exec(self):
            self.list_widget.item(0).setCheckState(Qt.CheckState.Checked)
            return QDialog.DialogCode.Accepted

    confirmations = []
    monkeypatch.setattr(schedule, "StudentSelectionDialog", AcceptedRoster)
    monkeypatch.setattr(
        schedule, "private_message", lambda *args, **kwargs: confirmations.append(args[2]) or True
    )
    window.schedule.lesson_rosters()
    assert len(confirmations) == 1 and "2 занятий" in confirmations[0]
    assert window.service.get_lesson_roster(lessons[0].id) == {student.id}
    assert window.service.get_lesson_roster(lessons[1].id) == set()
    assert window.service.get_lesson_roster(lessons[2].id) == {student.id}
