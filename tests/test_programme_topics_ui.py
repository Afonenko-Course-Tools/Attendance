"""Synthetic Qt paths for programme editing and chosen/new scheduled topics."""

from datetime import date

import pytest
import test_course_workspace_toolbar as toolbar_tests
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QSpinBox

from attendance_lab.presentation.preparation import FormDialog
from attendance_lab.presentation.programme_topics import ProgrammeTopicChoice


@pytest.fixture
def workspace(tmp_path):
    yield from toolbar_tests.workspace.__wrapped__(tmp_path)


def test_schedule_can_create_ninth_topic_and_cancel_does_not_create_it(workspace, monkeypatch):
    pane, service, course, _, roster, _ = workspace
    service.courses.add_topics(course.id, 4, 5)
    pane.show_course()
    existing = service.courses.list_topics(course.id)

    def fill(dialog, result):
        choice = dialog.findChild(ProgrammeTopicChoice)
        choice.selector.setCurrentIndex(choice.selector.findData(None))
        assert choice.number.value() == 9
        choice.title.setText("Новая девятая тема")
        return result

    monkeypatch.setattr(FormDialog, "exec", lambda d: fill(d, QDialog.DialogCode.Rejected))
    pane.schedule_dialog()
    assert service.courses.list_topics(course.id) == existing
    assert service.courses.list_lessons(course.id) == []
    monkeypatch.setattr(FormDialog, "exec", lambda d: fill(d, QDialog.DialogCode.Accepted))
    pane.schedule_dialog()
    new_topic = service.courses.list_topics(course.id)[-1]
    assert (new_topic.number, new_topic.title) == (9, "Новая девятая тема")
    occurrence = service.courses.list_lessons(course.id)[0]
    assert occurrence.topic_id == new_topic.id
    assert service.redesign.get_block(occurrence.block_id).title == new_topic.title
    assert len(service.redesign.get_block(occurrence.block_id).pair_ids) == roster.duration


def test_empty_programme_can_be_planned_with_new_topic(workspace, monkeypatch):
    pane, service, original, _, _, _ = workspace
    course = service.courses.save_course(
        original.discipline_id,
        original.academic_year_id,
        1,
        2,
        "lecture",
        1,
        "Новая лекционная программа",
    )
    pane.refresh()
    pane.courses.setCurrentIndex(pane.courses.findData(course.id))
    assert pane.tools["schedule"].isEnabled()

    def accept(dialog):
        choice = dialog.findChild(ProgrammeTopicChoice)
        assert choice.selector.currentData() is None and choice.number.value() == 1
        choice.title.setText("Первая выбранная тема")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", accept)
    pane.schedule_dialog()
    assert service.courses.list_topics(course.id)[0].title == "Первая выбранная тема"
    assert len(service.courses.list_lessons(course.id)) == 1


def test_programme_edit_fixes_number_and_uses_stable_selected_topic(workspace, monkeypatch):
    pane, service, course, _, _, _ = workspace
    pane.topics.selectRow(1)
    selected = service.courses.list_topics(course.id)[1]

    def edit(dialog):
        number = dialog.findChild(QSpinBox)
        assert number.value() == selected.number and not number.isEnabled()
        dialog.findChild(QLineEdit, "ProgrammeTopicTitle").setText(
            "Исправленное название общей темы"
        )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", edit)
    pane.topic_dialog()
    updated = service.courses.list_topics(course.id)[1]
    assert updated.id == selected.id and updated.number == selected.number
    assert updated.title == "Исправленное название общей темы"
    assert len(service.courses.list_topics(course.id)) == 3


def test_new_topic_number_follows_maximum_not_topic_count(workspace, monkeypatch):
    pane, service, course, _, _, _ = workspace
    service.courses.add_topic(course.id, 11, "Одиннадцатая тема")
    pane.show_course()

    def add(dialog):
        number = dialog.findChild(QSpinBox)
        assert number.value() == 12
        dialog.findChild(QLineEdit, "ProgrammeTopicTitle").setText("Двенадцатая тема")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", add)
    pane.topic_dialog(False)
    assert service.courses.list_topics(course.id)[-1].number == 12


def test_instance_topic_action_preserves_existing_evidence_and_other_instance(
    workspace, monkeypatch
):
    pane, service, course, students, roster, _ = workspace
    first = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    second = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 8), 1)
    block = service.redesign.get_block(first.block_id)
    service.redesign.create_manual_batch(
        {(students[0].student_id, pair) for pair in block.pair_ids}, "Исходная отметка"
    )
    before = {pair: service.redesign.pair_evidence(pair) for pair in block.pair_ids}
    pane.show_course()
    assert not pane.tools["lesson_topic"].isEnabled()
    pane.lessons.selectRow(0)
    assert pane.tools["lesson_topic"].isEnabled()
    target = service.courses.list_topics(course.id)[1]

    def choose(dialog):
        assert any(
            "зачёт посещения по теме" in label.text() for label in dialog.findChildren(QLabel)
        )
        choice = dialog.findChild(ProgrammeTopicChoice)
        assert choice.selector.currentData() == first.topic_id
        choice.selector.setCurrentIndex(choice.selector.findData(target.id))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", choose)
    pane.tools["lesson_topic"].trigger()
    assert service.courses.get_lesson(first.id).topic_id == target.id
    assert service.courses.get_lesson(second.id) == second
    assert {pair: service.redesign.pair_evidence(pair) for pair in block.pair_ids} == before
