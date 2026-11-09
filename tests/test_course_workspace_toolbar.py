"""Synthetic Qt coverage for usable course controls and meaningful absence filters."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
    one = service.courses.schedule(
        course.id, first.id, 1, datetime.now(ZoneInfo("Europe/Minsk")).date(), 1
    )
    series = service.courses.schedule_series(
        course.id,
        first.id,
        datetime.now(ZoneInfo("Europe/Minsk")).date() + timedelta(days=7),
        1,
        7,
        2,
        2,
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
    past = datetime.now(ZoneInfo("Europe/Minsk")).date() - timedelta(days=7)
    lesson = service.courses.schedule(course.id, first.id, 1, past, 1)
    service.courses.schedule(
        course.id,
        second.id,
        2,
        datetime.now(ZoneInfo("Europe/Minsk")).date() + timedelta(days=7),
        1,
    )
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
        lecture.id,
        roster.id,
        1,
        datetime.now(ZoneInfo("Europe/Minsk")).date() - timedelta(days=7),
        1,
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


def test_assigned_subgroup_is_used_when_legacy_snapshot_is_empty(workspace):
    from dataclasses import replace

    pane, service, course, students, first, _ = workspace
    occurrence = service.courses.schedule(
        course.id, first.id, 1, datetime.now(ZoneInfo("Europe/Minsk")).date() - timedelta(days=7), 1
    )
    block = service.redesign.get_block(occurrence.block_id)
    with service.repository.transaction():
        pair = service.get_lesson(block.pair_ids[0])
        service.repository.put_lesson(replace(pair, roster_fixed=True))
    pane.show_course()
    assert pane.missed_topics[students[0].student_id] == {occurrence.topic_id}
    assert pane.missed_topics[students[1].student_id] == set()
    assert not pane.journal_notice.isVisibleTo(pane.journal_pane)
    pane.journal.setCurrentCell(0, 7)
    assert not pane.tools["mark"].isEnabled()


def test_migrated_expected_ids_and_unknown_topics_have_distinct_counts(workspace):
    from dataclasses import replace

    pane, service, course, students, first, _ = workspace
    past = datetime.now(ZoneInfo("Europe/Minsk")).date() - timedelta(days=7)
    known = service.courses.schedule(course.id, first.id, 1, past, 1)
    with service.repository.transaction():
        unassigned = service.repository.rd_save_roster(
            replace(first, id=0, name="Состав не назначен", group_ids=(), student_ids=())
        )
    service.courses.add_roster(course.id, unassigned.id)
    unknown = service.courses.schedule(course.id, unassigned.id, 2, past, 3)
    for occurrence, ids in ((known, {students[0].student_id}), (unknown, set())):
        block = service.redesign.get_block(occurrence.block_id)
        with service.repository.transaction():
            for pair in block.pair_ids:
                service.repository.put_lesson(replace(service.get_lesson(pair), roster_fixed=True))
                service.repository.put_lesson_roster(pair, ids)
    pane.show_course()

    def counts():
        return {
            pane.journal.item(row, 0).data(Qt.ItemDataRole.UserRole): pane.journal.item(
                row, pane.journal.columnCount() - 1
            ).text()
            for row in range(pane.journal.rowCount())
        }

    assert counts() == {students[0].student_id: "≥1", students[1].student_id: "—"}
    pane.journal_status.setCurrentIndex(1)
    assert [
        pane.journal.item(row, 0).data(Qt.ItemDataRole.UserRole)
        for row in range(pane.journal.rowCount())
        if not pane.journal.isRowHidden(row)
    ] == [students[0].student_id]
    for occurrence in (known, unknown):
        block = service.redesign.get_block(occurrence.block_id)
        service.redesign.create_manual_batch(
            {(students[0].student_id, pair) for pair in block.pair_ids}, "Учебная отметка"
        )
    pane.update_attendance()
    assert counts() == {students[0].student_id: "0", students[1].student_id: "—"}


def test_journal_name_column_stays_compact_and_can_be_resized(workspace):
    from PySide6.QtWidgets import QHeaderView

    pane, _, _, _, _, _ = workspace
    pane.resize(1600, 700)
    pane.tabs.setCurrentWidget(pane.journal_pane)
    QApplication.processEvents()
    assert (
        pane.journal.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    )
    assert pane.journal.columnWidth(0) == 300
    pane.journal.setColumnWidth(0, 340)
    pane.show_course()
    assert pane.journal.columnWidth(0) == 340
    assert pane.journal.item(0, 0).toolTip() == pane.journal.item(0, 0).text()


def test_incomplete_absence_counts_sort_numerically(workspace):
    from PySide6.QtWidgets import QTableWidget

    from attendance_lab.presentation.course_journal import AbsenceCountItem

    listing = QTableWidget(3, 1)
    try:
        for row, count in enumerate((10, 0, 2)):
            listing.setItem(row, 0, AbsenceCountItem(str(row), set(range(count)), {100}))
        listing.sortItems(0, Qt.SortOrder.AscendingOrder)
        assert [listing.item(row, 0).text() for row in range(3)] == ["—", "≥2", "≥10"]
    finally:
        listing.close()


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
    service.courses.schedule(
        course.id, first.id, 1, datetime.now(ZoneInfo("Europe/Minsk")).date() - timedelta(days=7), 1
    )
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


def test_lecture_uses_selected_year_and_study_course_even_with_older_snapshot(workspace):
    pane, service, course, students, _, _ = workspace
    groups = service.redesign.list_groups()
    lecture = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 1, 2, "lecture", 1, "Исторический поток"
    )
    roster = service.courses.add_groups(lecture.id, {groups[0].id})
    service.courses.save_topic(lecture.id, 1, "Проведённая лекция")
    occurrence = service.courses.schedule(
        lecture.id,
        roster.id,
        1,
        datetime.now(ZoneInfo("Europe/Minsk")).date() - timedelta(days=7),
        1,
    )
    registration = service.courses.open_registration(occurrence.id)
    service.redesign.close_session(registration.id)
    service.courses.add_groups(lecture.id, {groups[1].id})
    pane.refresh()
    pane.courses.setCurrentIndex(pane.courses.findData(lecture.id))
    assert pane.missed_topics[students[0].student_id] == {occurrence.topic_id}
    assert pane.missed_topics[students[1].student_id] == {occurrence.topic_id}
    assert pane.missed_topics[students[2].student_id] == {occurrence.topic_id}


def test_fixed_empty_lecture_snapshot_uses_whole_selected_stream(workspace):
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
        lecture.id,
        roster.id,
        1,
        datetime.now(ZoneInfo("Europe/Minsk")).date() - timedelta(days=7),
        1,
    )
    block = service.redesign.get_block(occurrence.block_id)
    with service.repository.transaction():
        service.repository.put_lesson(
            replace(service.get_lesson(block.pair_ids[0]), roster_fixed=True)
        )
    pane.refresh()
    pane.courses.setCurrentIndex(pane.courses.findData(lecture.id))
    assert all(
        pane.missed_topics[student.student_id] == {occurrence.topic_id} for student in students
    )
    assert pane.journal_notice.isHidden()


def test_new_lecture_cohort_is_ready_without_an_artificial_subgroup(workspace):
    pane, service, original, _, _, _ = workspace
    lecture = service.courses.save_course(
        original.discipline_id, original.academic_year_id, 1, 2, "lecture", 1, "Общий поток"
    )
    service.courses.save_topic(lecture.id, 1, "Первая лекция")
    pane.refresh()
    pane.courses.setCurrentIndex(pane.courses.findData(lecture.id))
    assert pane.members.rowCount() == 1
    assert pane.members.item(0, 0).text() == "Общий поток"
    assert int(pane.members.item(0, 2).text()) == len(service.courses.list_students(lecture.id))
    assert pane.tools["schedule"].isEnabled()
    assert not pane.tools["groups"].isVisible()
    assert not pane.tools["subgroup"].isVisible()
    assert not pane.tools["edit_groups"].isVisible()


@pytest.mark.parametrize(("kind", "duration"), [("lecture", 1), ("lab", 2), ("seminar", 2)])
def test_scheduling_uses_course_format_and_duration_with_only_start_pair(
    workspace, monkeypatch, kind, duration
):
    from PySide6.QtWidgets import QFormLayout

    pane, service, original, _, _, _ = workspace
    course = service.courses.save_course(
        original.discipline_id,
        original.academic_year_id,
        1,
        2,
        kind,
        duration,
        "Курс с заданным форматом",
    )
    group = service.redesign.list_groups()[0]
    service.courses.add_groups(course.id, {group.id})
    service.courses.save_topic(course.id, 1, "Тема по общей программе")
    pane.refresh()
    pane.courses.setCurrentIndex(pane.courses.findData(course.id))

    def accept(dialog):
        fields = {}
        for row in range(dialog.form.rowCount()):
            label_item = dialog.form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field_item = dialog.form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if label_item and isinstance(label_item.widget(), QLabel) and field_item:
                fields[label_item.widget().text()] = field_item.widget()
        assert not {"Тип", "Формат", "Длительность", "Длительность, пар", "Пары"}.intersection(
            fields
        )
        slot = fields["Начальная пара"]
        assert isinstance(slot, QComboBox)
        assert slot.count() == 9 - duration
        assert slot.itemText(slot.count() - 1) == f"{9 - duration}-я пара · " + (
            "20:00" if duration == 1 else "18:30"
        )
        slot.setCurrentIndex(slot.count() - 1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", accept)
    pane.schedule_dialog()
    lessons = service.courses.list_lessons(course.id)
    assert len(lessons) == 1
    block = service.redesign.get_block(lessons[0].block_id)
    assert block.start_slot == 9 - duration
    assert len(block.pair_ids) == duration
    assert service.courses.get_course(course.id).kind == kind
    assert service.courses.get_course(course.id).duration == duration
