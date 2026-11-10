"""Stored-roster filtering never changes registration or infers membership."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.redesign import AcademicGroup, TeachingRoster
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.lesson_filtering import roster_caption
from attendance_lab.presentation.lesson_workspace import LessonWorkspace


@pytest.fixture
def roster_view(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = AttendanceService(
        SQLiteRepository(tmp_path / "synthetic-filter.sqlite3", mode="demo")
    )
    headers = [
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    ]
    service.redesign.apply_import(
        service.redesign.preview_import(
            headers,
            [
                ["1", "Вымышленный Алексей", "Тест", "Профиль", "2", "1", "0001"],
                ["2", "Вымышленная Анна", "Тест", "Профиль", "2", "2", "0002"],
            ],
            2026,
        )
    )
    groups = service.redesign.list_groups()
    discipline = service.save_discipline("Учебная лаборатория")
    course = service.courses.save_course(
        discipline.id, groups[0].academic_year_id, 1, 2, "lab", 1, "Учебный курс"
    )
    first = service.courses.add_groups(course.id, {groups[0].id}, "Лаборатория А")
    second = service.courses.add_groups(course.id, {groups[1].id}, "Лаборатория Б")
    service.courses.save_topic(course.id, 1, "Одна общая тема")
    today = datetime.now(ZoneInfo("Europe/Minsk")).date()
    occurrences = {}
    for roster, count in ((first, 12), (second, 2)):
        occurrences[roster.id] = [
            service.courses.schedule(course.id, roster.id, 1, today - timedelta(days=number + 1), 1)
            for number in range(count)
        ]
    controller = AttendanceController(service, DemoReader)
    widget = LessonWorkspace(service, controller, lambda: widget.refresh())
    widget.selected_course_id = course.id
    widget.today.setChecked(False)
    widget.refresh()
    widget.resize(980, 650)
    widget.show()
    app.processEvents()
    yield widget, service, controller, course, first, second, occurrences
    widget.close()
    controller.shutdown()
    service.close()
    app.processEvents()


def test_caption_uses_only_explicit_saved_groups():
    groups = [AcademicGroup(1, 1, 1, 2, 7), AcademicGroup(2, 1, 1, 2, 4)]
    roster = TeachingRoster(3, 1, 1, 1, "Среда, дневная", "lab", 1, "students", (1,), ("person",))
    assert roster_caption(roster, groups) == "Среда, дневная · гр. 7"
    no_groups = TeachingRoster(
        4, 1, 1, 1, "Историческая подгруппа", "lab", 1, "students", (), ("person",)
    )
    assert roster_caption(no_groups, groups) == "Историческая подгруппа"


def test_roster_filter_reduces_popup_and_preserves_block_identity(roster_view):
    widget, service, _, _, first, second, occurrences = roster_view
    assert widget.roster_filter_row.isVisible()
    assert widget.blocks.count() == 14
    assert any(
        "Лаборатория А · Тест · гр. 1" in widget.blocks.itemText(i)
        for i in range(widget.blocks.count())
    )
    assert any(
        "Лаборатория Б · Тест · гр. 2" in widget.blocks.itemText(i)
        for i in range(widget.blocks.count())
    )
    widget.roster_filter.setCurrentIndex(widget.roster_filter.findData(first.id))
    assert widget.blocks.count() == 12
    target = service.redesign.get_block(occurrences[first.id][4].block_id)
    widget.select_lesson(target.pair_ids[0])
    widget.refresh()
    assert widget.roster_filter.currentData() == first.id
    assert widget.viewed_block_id == target.id
    widget.roster_filter.setCurrentIndex(widget.roster_filter.findData(second.id))
    assert widget.blocks.count() == 2
    assert all("Лаборатория Б" in widget.blocks.itemText(i) for i in range(widget.blocks.count()))


def test_filter_navigation_reveals_target_without_switching_active_registration(roster_view):
    widget, service, controller, _, first, second, occurrences = roster_view
    active = service.redesign.get_block(occurrences[first.id][0].block_id)
    target = service.redesign.get_block(occurrences[second.id][1].block_id)
    controller.open_session(active.id, set(active.pair_ids))
    session_id = controller.active_session_id
    widget.roster_filter.setCurrentIndex(widget.roster_filter.findData(first.id))
    widget.today.setChecked(True)
    assert widget.blocks.count() == 0
    widget.select_lesson(target.pair_ids[0])
    assert widget.roster_filter.currentData() == second.id
    assert not widget.today.isChecked()
    assert widget.viewed_block_id == target.id
    assert controller.active_session_id == session_id
    assert controller.active_lesson_id in active.pair_ids


def test_lecture_hides_artificial_rosters_and_restores_course_filter(roster_view):
    widget, service, _, course, first, _, _ = roster_view
    widget.roster_filter.setCurrentIndex(widget.roster_filter.findData(first.id))
    lecture = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 1, 2, "lecture", 1, "Лекционный поток"
    )
    group = service.redesign.list_groups()[0]
    stream = service.courses.add_groups(lecture.id, {group.id}, "Исторический состав занятия")
    service.courses.save_topic(lecture.id, 1, "Лекция")
    service.courses.schedule(
        lecture.id, stream.id, 1, datetime.now(ZoneInfo("Europe/Minsk")).date(), 1
    )
    widget.selected_course_id = lecture.id
    widget.refresh()
    assert not widget.roster_filter_row.isVisible()
    assert "Исторический состав" not in widget.blocks.currentText()
    widget.selected_course_id = course.id
    widget.refresh()
    assert widget.roster_filter_row.isVisible() and widget.roster_filter.currentData() == first.id
    assert widget.blocks.count() == 12


def test_long_lesson_popup_is_scrollable_and_keyboard_selection_keeps_ids(roster_view):
    widget, _, _, _, first, _, _ = roster_view
    widget.roster_filter.setCurrentIndex(widget.roster_filter.findData(first.id))
    widget.blocks.showPopup()
    QApplication.processEvents()
    popup = widget.blocks.view().window()
    assert popup.height() <= (widget.blocks.fontMetrics().height() + 12) * 10 + 12
    assert widget.blocks.view().verticalScrollBar().maximum() > 0
    available = widget.blocks.screen().availableGeometry()
    assert available.contains(popup.frameGeometry())
    widget.blocks.hidePopup()
    widget.move(available.right() - 100, available.bottom() - 20)
    widget.blocks.showPopup()
    QApplication.processEvents()
    assert available.contains(popup.frameGeometry())
    index = widget.blocks.currentIndex()
    expected = widget.blocks.itemData(index + 1)
    QTest.keyClick(widget.blocks.view(), Qt.Key.Key_Down)
    QTest.keyClick(widget.blocks.view(), Qt.Key.Key_Return)
    assert widget.blocks.currentData() == expected
    assert widget.viewed_block_id == expected
    widget.blocks.hidePopup()
