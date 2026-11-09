"""Synthetic semester programme and whole-block workflow acceptance."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def course(tmp_path):
    s = AttendanceService(SQLiteRepository(tmp_path / "synthetic-course.sqlite3", mode="demo"))
    headers = [
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    ]
    s.redesign.apply_import(
        s.redesign.preview_import(
            headers,
            [
                ["1", "Вымышленный Первый", "Тест", "Профиль", "2", "1", "0001"],
                ["2", "Вымышленный Второй", "Тест", "Профиль", "2", "1", "0002"],
            ],
            2026,
        )
    )
    d = s.save_discipline("Учебный пример")
    g = s.redesign.list_groups()[0]
    c = s.courses.save_course(d.id, g.academic_year_id, 1, 2, "lab", 2, "Пример 2026")
    r = s.courses.add_groups(c.id, {g.id})
    for n in range(1, 5):
        s.courses.save_topic(c.id, n, f"Тема {n}")
    yield s, c, r
    s.close()


def test_programme_recurrence_move_and_whole_block(course):
    s, c, r = course
    lessons = s.courses.schedule_series(c.id, r.id, date(2026, 9, 1), 1, 7, 1, 3)
    assert [s.redesign.get_block(lesson.block_id).date for lesson in lessons] == [
        date(2026, 9, 1),
        date(2026, 9, 8),
        date(2026, 9, 15),
    ]
    s.courses.move_instance(lessons[1].id, date(2026, 9, 9), 3)
    assert s.redesign.get_block(lessons[0].block_id).date == date(2026, 9, 1)
    s.courses.change_recurrence(lessons[0].series_id, date(2026, 9, 9), 14)
    assert s.redesign.get_block(lessons[2].block_id).date == date(2026, 9, 23)
    assert s.repository.cw_recurrence_changes(lessons[0].series_id) == [(date(2026, 9, 9), 14)]
    s.courses.save_topic(c.id, 1, "Общая исправленная тема")
    b = s.redesign.get_block(lessons[0].block_id)
    assert b.title == "Общая исправленная тема"
    session = s.courses.open_registration(lessons[0].id)
    assert set(session.target_pair_ids) == set(b.pair_ids)


def test_topic_credit_is_course_scoped_and_zero_duplicate_scans(course):
    s, c, r = course
    lesson = s.courses.schedule(c.id, r.id, 1, date(2026, 9, 1), 1)
    student = s.courses.list_students(c.id)[0].student_id
    b = s.redesign.get_block(lesson.block_id)
    s.redesign.create_manual_batch({(student, p) for p in b.pair_ids}, "Синтетическая проверка")
    assert s.courses.attendance(c.id, student, lesson.topic_id)
    copied = s.courses.copy_course(c.id, c.academic_year_id, 2, "Новый семестр")
    assert not s.courses.list_lessons(copied.id)
    assert not s.courses.attendance(copied.id, student, s.courses.list_topics(copied.id)[0].id)


def test_missing_series_topic_rolls_back(course):
    s, c, r = course
    with pytest.raises(AttendanceError):
        s.courses.schedule_series(c.id, r.id, date(2026, 9, 1), 1, 7, 4, 2)
    assert not s.courses.list_lessons(c.id)
    assert not s.repository.cw_series(c.id)


def test_rectangle_targets_whole_blocks_and_validates_course(course):
    s, c, r = course
    first = s.courses.schedule(c.id, r.id, 1, date(2026, 9, 1), 1)
    second = s.courses.schedule(c.id, r.id, 2, date(2026, 9, 8), 1)
    students = [x.student_id for x in s.courses.list_students(c.id)]
    selected = {(student, lesson.topic_id) for student in students for lesson in (first, second)}
    s.courses.mark_topics(
        c.id,
        selected,
        {first.topic_id: first.id, second.topic_id: second.id},
        "Тест прямоугольника",
    )
    for lesson in (first, second):
        for pair in s.redesign.get_block(lesson.block_id).pair_ids:
            assert {e.student_id for e in s.redesign.pair_evidence(pair) if e.active} == set(
                students
            )
    with pytest.raises(AttendanceError):
        s.courses.mark_topics(
            c.id, selected, {first.topic_id: second.id, second.topic_id: second.id}, "Неверная тема"
        )


def test_qt_course_navigation_identity_sort_and_written_only(course, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from attendance_lab.application.controller import AttendanceController
    from attendance_lab.infrastructure.csv_io import CsvFiles
    from attendance_lab.infrastructure.readers import DemoReader
    from attendance_lab.presentation.admin import MainWindow

    app = QApplication.instance() or QApplication([])
    service, semester_course, roster = course
    occurrence = service.courses.schedule(
        semester_course.id, roster.id, 1, datetime.now(ZoneInfo("Europe/Minsk")).date(), 1
    )
    window = MainWindow(
        service,
        AttendanceController(service, DemoReader),
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "synthetic-course.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    window.show()
    app.processEvents()
    try:
        assert window.course_workspace.courses.isVisible()
        assert not window.workspace.search.isVisible()
        assert window.tabs.isTabVisible(window.tabs.indexOf(window.grading_page))
        journal = window.course_workspace.journal
        before = {
            journal.item(row, 5).data(Qt.ItemDataRole.UserRole) for row in range(journal.rowCount())
        }
        journal.sortItems(0, Qt.SortOrder.DescendingOrder)
        assert {
            journal.item(row, 5).data(Qt.ItemDataRole.UserRole) for row in range(journal.rowCount())
        } == before
        window.workspace.select_lesson(service.redesign.get_block(occurrence.block_id).pair_ids[0])
        assert window.course_workspace.grading.lesson_id == occurrence.id
        window.workspace.open_search()
        window.workspace.search.setText("Вымышленный")
        window.workspace.search.setCursorPosition(3)
        window.workspace.update_last_card()
        assert window.workspace.search.cursorPosition() == 3
        service.grading.add_work(semester_course.id, "Тестовая обязательная работа", 20)
        window.course_workspace.show_course()
        assert not service.courses.get_course(semester_course.id).reports_enabled
        assert window.tabs.isTabVisible(window.tabs.indexOf(window.grading_page))
    finally:
        window.close()
        app.processEvents()


def test_course_dimensions_locked_by_roster_and_roster_not_shared(course):
    service, semester_course, roster = course
    with pytest.raises(AttendanceError):
        service.courses.save_course(
            semester_course.discipline_id,
            semester_course.academic_year_id,
            semester_course.semester,
            semester_course.study_course,
            semester_course.kind,
            1,
            "Другая длительность",
            course_id=semester_course.id,
        )
    other = service.courses.save_course(
        semester_course.discipline_id,
        semester_course.academic_year_id,
        semester_course.semester,
        semester_course.study_course,
        semester_course.kind,
        2,
        "Другой курс",
    )
    with pytest.raises(AttendanceError):
        service.courses.add_roster(other.id, roster.id)
