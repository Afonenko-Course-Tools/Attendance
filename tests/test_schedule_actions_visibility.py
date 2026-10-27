"""Schedule controls remain reachable while only lesson rows scroll."""

from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.admin import MainWindow
from attendance_lab.presentation.theme import apply_light_theme


@pytest.mark.parametrize("size", [(980, 720), (1366, 768)])
def test_schedule_actions_stay_above_scrolling_rows_at_minimum_window(tmp_path, size):
    app = QApplication.instance() or QApplication([])
    original_font, original_palette = app.font(), app.palette()
    original_style = app.style().objectName()
    apply_light_theme(app)
    path = tmp_path / "synthetic-schedule.sqlite3"
    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    discipline = service.save_discipline(
        "Учебная дисциплина для проверки интерфейса",
        description="Подробное описание дисциплины. " * 100,
    )
    for index in range(40):
        start = datetime(2026, 9, 1, 8, tzinfo=UTC) + timedelta(days=index)
        service.save_lesson(
            discipline.id, start, start + timedelta(hours=1), "UTC", f"Занятие {index + 1}"
        )
    controller = AttendanceController(service, DemoReader)
    window = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=True,
        database_path=path,
        default_timezone="UTC",
    )
    window.event_timer.stop()
    controller.open_lesson(service.list_lessons(discipline.id)[0].id)
    window.registration_changed()
    try:
        window.tabs.setCurrentWidget(window.preparation)
        window.preparation.tabs.setCurrentIndex(0)
        window.resize(*size)
        window.show()
        app.processEvents()
        assert (window.width(), window.height()) == size
        pane = window.schedule
        assert pane.lessons.horizontalHeader().isVisible()
        assert pane.lessons.viewport().height() >= sum(
            pane.lessons.rowHeight(row) for row in (0, 1)
        )
        actions = {
            button.text(): button
            for button in pane.findChildren(QPushButton)
            if button.text()
            in (
                "Добавить занятие",
                "Изменить занятие",
                "Состав выбранных занятий…",
                "Открыть регистрацию",
                "Приватный журнал",
            )
        }
        assert len(actions) == 5
        table_top = pane.lessons.mapTo(window, QPoint()).y()
        for button in actions.values():
            top = button.mapTo(window, QPoint())
            assert button.isVisibleTo(window)
            assert window.rect().contains(top)
            assert window.rect().contains(button.mapTo(window, button.rect().bottomRight()))
            assert top.y() + button.height() <= table_top
            assert button.width() >= button.sizeHint().width()
        assert not pane.parentWidget().findChildren(QScrollArea)
        assert pane.lessons.verticalScrollBar().maximum() > 0
        pane.lessons.scrollToBottom()
        app.processEvents()
        for button in actions.values():
            assert button.visibleRegion().boundingRect().contains(button.rect())
        pane.lessons.setFocus()
        pane.lessons.selectRow(0)
        QTest.keyClick(pane.lessons, Qt.Key.Key_Down)
        selected = pane.selected_lesson_id()
        assert selected == service.list_lessons(discipline.id)[1].id
        opened = []
        pane.open_lesson = opened.append
        QTest.mouseClick(actions["Открыть регистрацию"], Qt.MouseButton.LeftButton)
        assert opened == [selected]
    finally:
        window.close()
        service.close()
        app.setStyle(original_style)
        app.setFont(original_font)
        app.setPalette(original_palette)
        app.processEvents()
