"""Synthetic Qt/use-case checks for marking attendance directly from journal cells."""

from datetime import UTC, date, datetime

import pytest
import test_course_workflow as course_tests
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QWidget,
)

from attendance_lab.presentation.journal_marking import DEFAULT_REASON, mark_journal_cells
from attendance_lab.presentation.preparation import FormDialog


@pytest.fixture
def marking(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    fixture = course_tests.course.__wrapped__(tmp_path)
    service, course, roster = next(fixture)
    parent = QWidget()
    changed, messages = [], []
    monkeypatch.setattr(
        "attendance_lab.presentation.common.private_message",
        lambda _parent, _title, text, **_: messages.append(text),
    )
    yield parent, service, course, roster, changed, messages
    parent.close()
    app.processEvents()
    try:
        next(fixture)
    except StopIteration:
        pass


def manual_students(service, block):
    return {
        evidence.student_id
        for pair in block.pair_ids
        for evidence in service.redesign.pair_evidence(pair)
        if evidence.active and evidence.kind == "manual"
    }


def test_unique_instances_save_selected_students_topics_and_full_blocks_without_form(
    marking, monkeypatch
):
    parent, service, course, roster, changed, messages = marking
    first = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    second = service.courses.schedule(course.id, roster.id, 2, date(2026, 9, 8), 3)
    students = service.courses.list_students(course.id)
    selected = {(students[0].student_id, first.topic_id), (students[1].student_id, second.topic_id)}
    monkeypatch.setattr(
        FormDialog, "exec", lambda _: pytest.fail("Unique instances must save without a form")
    )
    assert mark_journal_cells(parent, service, course.id, selected, lambda: changed.append(True))
    assert changed == [True] and not messages
    first_block = service.redesign.get_block(first.block_id)
    second_block = service.redesign.get_block(second.block_id)
    assert manual_students(service, first_block) == {students[0].student_id}
    assert manual_students(service, second_block) == {students[1].student_id}
    for pair in (*first_block.pair_ids, *second_block.pair_ids):
        assert len(service.redesign.pair_evidence(pair)) == 1
    assert service.redesign.list_manual_batches()[0].reason == DEFAULT_REASON


def test_repeat_confirmed_attendance_is_noop_without_extra_evidence(marking):
    parent, service, course, roster, changed, _ = marking
    occurrence = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    student = service.courses.list_students(course.id)[0].student_id
    selected = {(student, occurrence.topic_id)}

    def callback():
        changed.append(True)

    assert mark_journal_cells(parent, service, course.id, selected, callback)
    block = service.redesign.get_block(occurrence.block_id)
    before = {pair: service.redesign.pair_evidence(pair) for pair in block.pair_ids}
    assert not mark_journal_cells(parent, service, course.id, selected, callback)
    assert {pair: service.redesign.pair_evidence(pair) for pair in block.pair_ids} == before
    assert len(service.redesign.list_manual_batches()) == 1 and changed == [True]


def ambiguous(marking):
    parent, service, course, roster, changed, messages = marking
    first = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    second = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 15), 3)
    student = service.courses.list_students(course.id)[0]
    return first, second, student


def test_multiple_instances_require_explicit_id_and_show_person_date_time_line(
    marking, monkeypatch
):
    parent, service, course, roster, changed, messages = marking
    first, second, student = ambiguous(marking)

    def choose(dialog):
        control = dialog.findChild(QComboBox, f"TopicOccurrence:{first.topic_id}")
        assert control.currentData() is None
        save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        assert not save.isEnabled()
        assert any(
            student.full_name in label.text() and "Тема 1" in label.text()
            for label in dialog.findChildren(QLabel)
        )
        index = control.findData(second.id)
        assert "15.09.2026 12:00–15:10" in control.itemText(index)
        assert roster.name in control.itemText(index)
        reason = dialog.findChild(QLineEdit, "JournalMarkReason")
        assert reason.text() == DEFAULT_REASON
        control.setCurrentIndex(index)
        assert save.isEnabled()
        reason.setText("Подтверждение после проверки журнала")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", choose)
    assert mark_journal_cells(
        parent,
        service,
        course.id,
        {(student.student_id, first.topic_id)},
        lambda: changed.append(True),
    )
    assert manual_students(service, service.redesign.get_block(first.block_id)) == set()
    assert manual_students(service, service.redesign.get_block(second.block_id)) == {
        student.student_id
    }
    assert (
        service.redesign.list_manual_batches()[0].reason == "Подтверждение после проверки журнала"
    )
    assert changed == [True] and not messages


def test_cancelled_instance_choice_does_not_write(marking, monkeypatch):
    parent, service, course, _, changed, _ = marking
    first, _, student = ambiguous(marking)
    monkeypatch.setattr(FormDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    assert not mark_journal_cells(
        parent,
        service,
        course.id,
        {(student.student_id, first.topic_id)},
        lambda: changed.append(True),
    )
    assert service.redesign.list_manual_batches() == [] and changed == []


def test_cross_course_topic_is_rejected_before_any_write(marking):
    parent, service, course, _, changed, messages = marking
    other = service.courses.copy_course(course.id, course.academic_year_id, 2, "Другой семестр")
    foreign_topic = service.courses.list_topics(other.id)[0]
    student = service.courses.list_students(course.id)[0].student_id
    assert not mark_journal_cells(
        parent, service, course.id, {(student, foreign_topic.id)}, lambda: changed.append(True)
    )
    assert service.redesign.list_manual_batches() == [] and changed == []
    assert "не относится к этому курсу" in messages[0]


def test_topic_without_actual_instance_is_rejected_without_partial_write(marking):
    parent, service, course, roster, changed, messages = marking
    scheduled = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    unscheduled = service.courses.list_topics(course.id)[1]
    student = service.courses.list_students(course.id)[0].student_id
    assert not mark_journal_cells(
        parent,
        service,
        course.id,
        {(student, scheduled.topic_id), (student, unscheduled.id)},
        lambda: changed.append(True),
    )
    assert service.redesign.list_manual_batches() == [] and changed == []
    assert "ещё нет занятия в расписании" in messages[0]


def test_known_card_while_choosing_prevents_redundant_manual_evidence(marking, monkeypatch):
    parent, service, course, _, changed, messages = marking
    first, second, student = ambiguous(marking)

    def choose_after_scan(dialog):
        service.bind_card("0011AB", student.student_id)
        session = service.courses.open_registration(first.id)
        service.redesign.record_session_scan(
            session.id, "0011AB", datetime(2026, 9, 1, 6, 1, tzinfo=UTC)
        )
        service.redesign.close_session(session.id)
        control = dialog.findChild(QComboBox, f"TopicOccurrence:{first.topic_id}")
        control.setCurrentIndex(control.findData(second.id))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", choose_after_scan)
    assert not mark_journal_cells(
        parent,
        service,
        course.id,
        {(student.student_id, first.topic_id)},
        lambda: changed.append(True),
    )
    assert service.redesign.list_manual_batches() == [] and changed == [] and messages == []
    assert (student.student_id, first.topic_id) in service.courses.attendance_projection(course.id)


def test_confirmed_cells_are_skipped_while_remaining_selected_cells_are_marked(marking):
    parent, service, course, roster, changed, _ = marking
    occurrence = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    first, second = service.courses.list_students(course.id)
    assert mark_journal_cells(
        parent,
        service,
        course.id,
        {(first.student_id, occurrence.topic_id)},
        lambda: changed.append(True),
    )
    block = service.redesign.get_block(occurrence.block_id)
    original = service.redesign.evidence(first.student_id, block.pair_ids[0])
    assert mark_journal_cells(
        parent,
        service,
        course.id,
        {(first.student_id, occurrence.topic_id), (second.student_id, occurrence.topic_id)},
        lambda: changed.append(True),
    )
    assert service.redesign.evidence(first.student_id, block.pair_ids[0]) == original
    assert len(service.redesign.list_manual_batches()) == 2
    assert manual_students(service, block) == {first.student_id, second.student_id}


def test_unassigned_course_can_record_actual_attendance_without_enrolling_student(marking):
    from attendance_lab.domain.redesign import TeachingRoster

    parent, service, original, _, changed, messages = marking
    course = service.courses.save_course(
        original.discipline_id,
        original.academic_year_id,
        1,
        2,
        "lab",
        2,
        "Курс без назначенного состава",
    )
    with service.repository.transaction():
        roster = service.repository.rd_save_roster(
            TeachingRoster(
                0,
                course.discipline_id,
                course.academic_year_id,
                1,
                "Историческая линия",
                "lab",
                2,
                "students",
                (),
                (),
            )
        )
        service.courses.add_roster(course.id, roster.id)
    topic = service.courses.save_topic(course.id, 1, "Фактическое посещение")
    service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    student = service.list_students()[0]
    assert service.courses.list_students(course.id) == []
    assert mark_journal_cells(
        parent, service, course.id, {(student.id, topic.id)}, lambda: changed.append(True)
    )
    assert service.courses.list_students(course.id) == []
    assert {s.student_id for s in service.courses.list_observed_students(course.id)} == {student.id}
    assert changed == [True] and messages == []
