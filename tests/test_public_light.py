"""Public light display is readable, private, and reachable from the new menu."""

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import PublicResult
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.admin import MainWindow
from attendance_lab.presentation.public import PublicWindow


def _contrast(first: QColor, second: QColor) -> float:
    def luminance(color):
        values = [
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in (color.redF(), color.greenF(), color.blueF())
        ]
        return sum(v * w for v, w in zip(values, (0.2126, 0.7152, 0.0722), strict=True))

    values = sorted((luminance(first), luminance(second)))
    return (values[1] + 0.05) / (values[0] + 0.05)


@pytest.mark.parametrize("kind", ["idle", "closed", "success", "repeat", "error", "display_error"])
def test_every_public_state_keeps_light_background_and_readable_text(kind):
    app = QApplication.instance() or QApplication([])
    window = PublicWindow()
    window.resize(980, 720)
    window.show()
    window.set_lesson("Учебный курс · вымышленные данные", registration_open=True)
    if kind == "display_error":
        window.display_update_error()
    elif kind != "idle":
        window.display(
            PublicResult(
                kind,
                "UID=SYNTHETIC /private/synthetic.sqlite3",
                "Вымышленный студент",
                datetime.now(UTC),
            )
        )
    app.processEvents()
    background = window.grab().toImage().pixelColor(5, 5)
    assert background.lightness() >= 230
    for label in (window.context, window.headline, window.person, window.detail):
        if label.text():
            assert (
                _contrast(label.palette().color(QPalette.ColorRole.WindowText), background) >= 4.5
            )
    text = "\n".join(label.text() for label in window.findChildren(QLabel))
    assert "SYNTHETIC" not in text and "/private/" not in text and ".sqlite3" not in text
    if kind in ("error", "display_error", "closed"):
        assert window.person.text() == ""
    window.close()


def test_timeout_and_fullscreen_escape_preserve_public_privacy():
    app = QApplication.instance() or QApplication([])
    window = PublicWindow()
    window.set_lesson("Учебный курс", registration_open=True)
    window.set_timeout_seconds(1)
    window.display(PublicResult("success", "secret ignored", "Вымышленный студент"))
    window.showFullScreen()
    app.processEvents()
    assert window.isFullScreen()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    app.processEvents()
    assert not window.isFullScreen()
    QTest.qWait(1150)
    assert not window.person.text()
    assert window.headline.text() == "Приложите карту"
    window.display(PublicResult("repeat", "", "Вымышленный студент"))
    window.set_lesson("", registration_open=False)
    assert not window.person.text() and not window.timer.isActive()
    assert window.headline.text() == "Регистрация закрыта"
    window.close()


def test_new_main_menu_opens_a_separate_light_public_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = AttendanceService(
        SQLiteRepository(tmp_path / "synthetic-public.sqlite3", mode="demo")
    )
    controller = AttendanceController(service, DemoReader)
    window = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "synthetic-public.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    window.show()
    app.processEvents()
    try:
        menu = next(
            action.menu() for action in window.menuBar().actions() if action.text() == "Вид"
        )
        action = next(action for action in menu.actions() if action.text() == "Публичное окно")
        assert not window.public_window.isVisible()
        action.trigger()
        app.processEvents()
        assert window.public_window.isWindow() and window.public_window.isVisible()
        assert window.public_window.grab().toImage().pixelColor(5, 5).lightness() >= 230
        assert not window.logs_window.isVisible()
    finally:
        window.close()
        service.close()
        app.processEvents()


def test_unknown_card_keeps_saved_mark_and_later_matching_message(tmp_path):
    from datetime import timedelta

    app = QApplication.instance() or QApplication([])
    service = AttendanceService(SQLiteRepository(tmp_path / "pending-public.sqlite3", mode="demo"))
    controller = AttendanceController(service, DemoReader)
    window = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "pending-public.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    try:
        discipline = service.save_discipline("Учебный предмет")
        now = datetime.now(UTC)
        lesson = service.save_lesson(
            discipline.id, now - timedelta(minutes=1), now + timedelta(minutes=20), "Europe/Minsk"
        )
        controller.connect_reader(controller.available_readers()[0])
        controller.open_lesson(lesson.id)
        controller.simulate("001122")
        window.process_events()
        assert len(service.lesson_log(lesson.id)) == 1
        assert service.lesson_log(lesson.id)[0].student_id is None
        assert window.public_window.headline.text() == "Отметка сохранена"
        assert "Преподаватель сопоставит позже" in window.public_window.detail.text()
        assert window.public_window.person.text() == ""
        assert "001122" not in "\n".join(
            label.text() for label in window.public_window.findChildren(QLabel)
        )
        student = service.save_student("Вымышленный студент", student_id="SYNTHETIC-PENDING")
        service.bind_card("001122", student.id)
        assert len(service.lesson_log(lesson.id)) == 1
        assert service.lesson_log(lesson.id)[0].student_id == student.id
    finally:
        window.close()
        service.close()
        app.processEvents()
