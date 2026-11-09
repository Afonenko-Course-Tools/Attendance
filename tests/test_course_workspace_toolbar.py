"""Synthetic Qt coverage for usable course controls and meaningful absence filters."""

from datetime import date, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QWidget

from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.course_workspace import CourseWorkspace
from attendance_lab.presentation.preparation import FormDialog


class LessonStub(QWidget):
    def __init__(self):
        super().__init__()
        self.blocks = QComboBox()
        self.viewed_block_id = None
        self.opened = []

    def refresh(self):
        pass

    def select_lesson(self, lesson_id):
        self.opened.append(lesson_id)


@pytest.fixture
def workspace(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = AttendanceService(
        SQLiteRepository(tmp_path / "synthetic-toolbar.sqlite3", mode="demo")
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
    rows = [
        ["1", "Первый Тестовый", "Учебная специальность", "Профиль", "2", "1", "0001"],
        ["2", "Второй Тестовый", "Учебная специальность", "Профиль", "2", "1", "0002"],
        ["3", "Третий Тестовый", "Учебная специальность", "Профиль", "2", "2", "0003"],
    ]
    service.redesign.apply_import(service.redesign.preview_import(headers, rows, 2026))
    discipline = service.save_discipline("Учебный пример")
    group = service.redesign.list_groups()[0]
    semester_course = service.courses.save_course(
        discipline.id, group.academic_year_id, 1, 2, "lab", 2, "Тестовый курс"
    )
    students = sorted(service.redesign.list_student_contexts(), key=lambda s: s.record_book_number)
    first = service.courses.add_groups(
        semester_course.id, {group.id}, "Первая подгруппа", {students[0].student_id}
    )
    second = service.courses.add_groups(
        semester_course.id, {group.id}, "Вторая подгруппа", {students[1].student_id}
    )
    for number in (1, 2, 3):
        service.courses.save_topic(semester_course.id, number, f"Полное название темы {number}")
    lesson_view = LessonStub()
    pane = CourseWorkspace(service, lambda: None, lesson_view, QWidget())
    pane.resize(980, 650)
    pane.refresh()
    pane.show()
    app.processEvents()
    yield pane, service, semester_course, students, first, second
    pane.close()
    lesson_view.close()
    service.close()
    app.processEvents()


def test_toolbar_selection_enables_real_targets_and_series_only(workspace):
    pane, service, course, _, first, _ = workspace
    assert pane.tools["schedule"].isEnabled()
    assert not pane.tools["view"].isEnabled()
    assert not pane.tools["move"].isEnabled()
    assert not pane.tools["edit_topic"].isEnabled()
    pane.topics.selectRow(0)
    assert pane.tools["edit_topic"].isEnabled()
    one = service.courses.schedule(course.id, first.id, 1, date.today(), 1)
    series = service.courses.schedule_series(
        course.id, first.id, date.today() + timedelta(days=7), 1, 7, 2, 2
    )
    pane.show_course()
    pane.lessons.selectRow(0)
    assert pane.selected_lesson() == one.id
    assert pane.tools["view"].isEnabled() and pane.tools["move"].isEnabled()
    assert not pane.tools["recurrence"].isEnabled()
    pane.tools["view"].trigger()
    assert pane.lesson_workspace.opened == list(
        service.redesign.get_block(one.block_id).pair_ids[:1]
    )
    pane.lessons.selectRow(1)
    assert pane.selected_lesson() == series[0].id
    assert pane.tools["recurrence"].isEnabled()
    pane.lessons.clearSelection()
    assert not pane.tools["delete"].isEnabled()
    pane.journal.setCurrentCell(0, pane.journal.columnCount() - 1)
    assert not pane.tools["mark"].isEnabled()
    pane.journal.setCurrentCell(0, 5)
    assert pane.tools["mark"].isEnabled()


def test_absences_ignore_future_and_other_subgroups_and_refresh_after_mark(workspace):
    pane, service, course, students, first, second = workspace
    past = date.today() - timedelta(days=7)
    lesson = service.courses.schedule(course.id, first.id, 1, past, 1)
    service.courses.schedule(course.id, second.id, 2, date.today() + timedelta(days=7), 1)
    pane.show_course()
    assert pane.missed_topics[students[0].student_id] == {lesson.topic_id}
    assert pane.missed_topics[students[1].student_id] == set()
    pane.journal_status.setCurrentIndex(1)

    def visible():
        return [
            pane.journal.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(pane.journal.rowCount())
            if not pane.journal.isRowHidden(row)
        ]

    assert visible() == [students[0].student_id]
    pane.absence_threshold.setValue(2)
    assert visible() == []
    pane.absence_threshold.setValue(1)
    block = service.redesign.get_block(lesson.block_id)
    service.redesign.create_manual_batch(
        {(students[0].student_id, pair) for pair in block.pair_ids}, "Учебное подтверждение"
    )
    pane.update_attendance(set(block.pair_ids))
    assert visible() == []
    assert pane.missed_topics[students[0].student_id] == set()
    assert pane.journal.horizontalHeaderItem(5).toolTip() == "Тема 1: Полное название темы 1"


def test_lecture_has_one_stream_no_subgroup_ui_and_cancel_does_not_write(workspace, monkeypatch):
    pane, service, course, students, _, _ = workspace
    lecture = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 1, 2, "lecture", 1, "Лекционный поток"
    )
    groups = {g.id for g in service.redesign.list_groups()}
    roster = service.courses.add_groups(lecture.id, groups)
    service.courses.save_topic(lecture.id, 1, "Общая тема")
    occurrence = service.courses.schedule(
        lecture.id, roster.id, 1, date.today() - timedelta(days=7), 1
    )
    pane.refresh()
    pane.courses.setCurrentIndex(pane.courses.findData(lecture.id))
    assert pane.members.rowCount() == 1 and pane.members.item(0, 0).text() == "Общий поток"
    assert not pane.tools["subgroup"].isVisible()
    pane.journal_context.setChecked(True)
    assert pane.journal.isColumnHidden(2)
    assert pane.journal_sort.findData(2) == -1
    assert all(
        pane.missed_topics[student.student_id] == {occurrence.topic_id} for student in students
    )
    called = []
    monkeypatch.setattr(service.courses, "lecture_roster", lambda key: called.append(key))
    labels = []

    def cancel(dialog):
        labels.extend(label.text() for label in dialog.findChildren(QLabel))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(FormDialog, "exec", cancel)
    pane.schedule_dialog()
    assert called == []
    assert "Состав" not in labels


def test_unknown_subgroup_snapshot_is_explained_and_not_counted(workspace):
    from dataclasses import replace

    pane, service, course, students, first, _ = workspace
    occurrence = service.courses.schedule(
        course.id, first.id, 1, date.today() - timedelta(days=7), 1
    )
    block = service.redesign.get_block(occurrence.block_id)
    with service.repository.transaction():
        pair = service.get_lesson(block.pair_ids[0])
        service.repository.put_lesson(replace(pair, roster_fixed=True))
    pane.show_course()
    assert pane.missed_topics[students[0].student_id] == set()
    assert pane.journal_notice.isVisibleTo(pane.journal_pane)
    assert "не указан состав" in pane.journal_notice.text()
    pane.journal.setCurrentCell(0, 7)
    assert not pane.tools["mark"].isEnabled()


def test_new_and_copied_course_become_the_selected_course(workspace, monkeypatch):
    from PySide6.QtWidgets import QFormLayout, QLineEdit

    pane, service, original, _, _, _ = workspace
    pane.changed = pane.refresh
    saved_name = ["Новый курс из формы"]

    def accept(dialog):
        name = dialog.form.itemAt(0, QFormLayout.ItemRole.FieldRole).widget()
        assert isinstance(name, QLineEdit)
        name.setText(saved_name[0])
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", accept)
    pane.new_course()
    created = next(
        course for course in service.courses.list_courses() if course.name == saved_name[0]
    )
    assert pane.current_course_id == created.id and created.id != original.id
    pane.courses.setCurrentIndex(pane.courses.findData(original.id))
    saved_name[0] = "Копия исходного курса"
    pane.copy_course()
    copied = next(
        course for course in service.courses.list_courses() if course.name == saved_name[0]
    )
    assert pane.current_course_id == copied.id and copied.id != original.id


def test_filtering_selected_student_disables_mark_without_losing_identity(workspace, monkeypatch):
    pane, service, course, students, first, _ = workspace
    service.courses.schedule(course.id, first.id, 1, date.today() - timedelta(days=7), 1)
    pane.show_course()
    row = next(
        row
        for row in range(pane.journal.rowCount())
        if pane.journal.item(row, 0).data(Qt.ItemDataRole.UserRole) == students[0].student_id
    )
    pane.journal.setCurrentCell(row, 5)
    selected_identity = pane.journal.item(row, 5).data(Qt.ItemDataRole.UserRole)
    assert pane.tools["mark"].isEnabled()
    pane.journal_search.setText("Несуществующее имя")
    assert pane.journal.isRowHidden(row)
    assert not pane.tools["mark"].isEnabled()

    def unexpected_dialog(_dialog):
        raise AssertionError("Hidden cells must not open a confirmation")

    monkeypatch.setattr(FormDialog, "exec", unexpected_dialog)
    pane.mark_rectangle()
    pane.journal_search.clear()
    assert pane.journal.item(row, 5).data(Qt.ItemDataRole.UserRole) == selected_identity
    assert pane.tools["mark"].isEnabled()


def test_fixed_lecture_snapshot_does_not_add_absence_for_later_joiner(workspace):
    pane, service, course, students, _, _ = workspace
    groups = service.redesign.list_groups()
    lecture = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 1, 2, "lecture", 1, "Исторический поток"
    )
    roster = service.courses.add_groups(lecture.id, {groups[0].id})
    service.courses.save_topic(lecture.id, 1, "Проведённая лекция")
    occurrence = service.courses.schedule(
        lecture.id, roster.id, 1, date.today() - timedelta(days=7), 1
    )
    registration = service.courses.open_registration(occurrence.id)
    service.redesign.close_session(registration.id)
    service.courses.add_groups(lecture.id, {groups[1].id})
    pane.refresh()
    pane.courses.setCurrentIndex(pane.courses.findData(lecture.id))
    assert pane.missed_topics[students[0].student_id] == {occurrence.topic_id}
    assert pane.missed_topics[students[1].student_id] == {occurrence.topic_id}
    assert pane.missed_topics[students[2].student_id] == set()


def test_fixed_empty_lecture_snapshot_is_unknown_instead_of_whole_stream(workspace):
    from dataclasses import replace

    pane, service, course, students, _, _ = workspace
    lecture = service.courses.save_course(
        course.discipline_id,
        course.academic_year_id,
        1,
        2,
        "lecture",
        1,
        "Неуточнённый исторический поток",
    )
    roster = service.courses.add_groups(lecture.id, {g.id for g in service.redesign.list_groups()})
    service.courses.save_topic(lecture.id, 1, "Лекция без снимка состава")
    occurrence = service.courses.schedule(
        lecture.id, roster.id, 1, date.today() - timedelta(days=7), 1
    )
    block = service.redesign.get_block(occurrence.block_id)
    with service.repository.transaction():
        service.repository.put_lesson(
            replace(service.get_lesson(block.pair_ids[0]), roster_fixed=True)
        )
    pane.refresh()
    pane.courses.setCurrentIndex(pane.courses.findData(lecture.id))
    assert all(pane.missed_topics[student.student_id] == set() for student in students)
    assert not pane.journal_notice.isHidden()
