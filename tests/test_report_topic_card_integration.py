"""Live topic suggestions operate within the real report-card draft."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox
from test_grading import graded_course, qapp  # noqa: F401

from attendance_lab.domain.grading import CRITERIA, ReportCard
from attendance_lab.infrastructure.report_topic_catalog import load_report_topic_catalogs
from attendance_lab.presentation.grading import ReportCardDialog


def type_query(editor, text):
    editor.setFocus()
    editor.selectAll()
    QApplication.clipboard().setText(text)
    QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)


@pytest.fixture
def historical_card(graded_course, qapp):  # noqa: F811
    service, course, first, second = graded_course
    service.report_topic_catalogs = load_report_topic_catalogs()
    service.save_discipline("Кибербезопасность", discipline_id=course.discipline_id)
    student = service.grading.students(course.id)[0]
    levels = tuple(c.steps - 1 for c in CRITERIA)
    saved = ReportCard(
        course.id, student.student_id, first.id, "Своя сохранённая тема", True, levels
    )
    service.grading.save_report(saved)
    block = service.redesign.get_block(first.block_id)
    service.lifecycle.delete_lesson(block.pair_ids[0], "Синтетическая проверка истории")
    dialog = ReportCardDialog(service.grading, course.id, student.student_id, second.id)
    dialog.resize(750, 700)
    dialog.show()
    dialog.activateWindow()
    qapp.processEvents()
    yield service, saved, dialog, qapp
    dialog.close()


def test_live_choice_preserves_unsaved_levels_and_historical_instance(historical_card):
    service, saved, dialog, app = historical_card
    dialog.sliders[13].setValue(0)
    draft = dialog.criteria.levels()
    type_query(dialog.topic.editor, "данных зашита")
    app.processEvents()
    assert dialog.topic.results.isVisible() and dialog.topic.results.count() >= 1
    assert dialog.topic.results.currentItem() is None
    QTest.keyClick(dialog.topic.editor, Qt.Key.Key_Return)
    assert dialog.isVisible() and service.grading.report(saved.course_id, saved.student_id) == saved
    QTest.keyClick(dialog.topic.editor, Qt.Key.Key_Down)
    title = dialog.topic.results.currentItem().data(Qt.ItemDataRole.UserRole)
    QTest.keyClick(dialog.topic.results, Qt.Key.Key_Return)
    assert dialog.topic.currentText() == title and not dialog.topic.results.isVisible()
    assert dialog.criteria.levels() == draft and dialog.lesson.currentData() == saved.lesson_id
    assert "(удалено)" in dialog.lesson.currentText() and not dialog.lesson.isEnabled()
    dialog.written.setChecked(False)
    dialog.written.setChecked(True)
    assert dialog.criteria.levels() == draft
    dialog.save()
    result = service.grading.report(saved.course_id, saved.student_id)
    assert result.levels == draft and result.lesson_id == saved.lesson_id and result.topic == title


def test_search_open_criterion_click_and_custom_save_each_need_one_click(historical_card):
    service, saved, dialog, app = historical_card
    type_query(dialog.topic.editor, "защита")
    app.processEvents()
    assert dialog.topic.results.isVisible()
    button = dialog.sliders[13].buttons[0]
    dialog.criteria.ensureWidgetVisible(button)
    app.processEvents()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert dialog.sliders[13].value() == 0 and not dialog.topic.results.isVisible()
    type_query(dialog.topic.editor, "Своя новая согласованная тема")
    app.processEvents()
    assert dialog.topic.notice.isVisible()
    box = dialog.findChild(QDialogButtonBox)
    QTest.mouseClick(box.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)
    app.processEvents()
    assert dialog.result() == QDialog.DialogCode.Accepted
    result = service.grading.report(saved.course_id, saved.student_id)
    assert result.topic == "Своя новая согласованная тема" and result.levels[13] == 0
    assert result.lesson_id == saved.lesson_id


def test_escape_first_dismisses_suggestions_then_rejects_without_saving(historical_card):
    service, saved, dialog, app = historical_card
    type_query(dialog.topic.editor, "защита")
    app.processEvents()
    QTest.keyClick(dialog.topic.editor, Qt.Key.Key_Down)
    QTest.keyClick(dialog.topic.results, Qt.Key.Key_Escape)
    assert dialog.isVisible() and not dialog.topic.results.isVisible()
    assert dialog.topic.currentText() == "защита" and dialog.topic.editor.hasFocus()
    QTest.keyClick(dialog.topic.editor, Qt.Key.Key_Escape)
    assert not dialog.isVisible() and dialog.result() == QDialog.DialogCode.Rejected
    assert service.grading.report(saved.course_id, saved.student_id) == saved


@pytest.mark.parametrize("width,height", [(640, 650), (750, 700)])
def test_saved_card_live_results_and_buttons_fit_small_laptop(historical_card, width, height):
    _, _, dialog, app = historical_card
    longest = max(dialog.topic.index.items, key=lambda entry: len(entry.title)).title
    dialog.topic.setCurrentText(longest)
    dialog.resize(width, height)
    dialog.topic.editor.setFocus()
    QTest.keyClick(dialog.topic.editor, Qt.Key.Key_Down)
    for _ in range(3):
        app.processEvents()
        dialog.layout().activate()
    assert dialog.size().toTuple() == (width, height)
    assert dialog.topic.results.isVisible() and dialog.topic.results.height() <= 200
    assert dialog.criteria.height() >= 180
    assert dialog.topic.results.horizontalScrollBar().maximum() == 0
    box = dialog.findChild(QDialogButtonBox)
    for button in box.buttons():
        rectangle = button.rect().translated(button.mapTo(dialog, QPoint()))
        assert button.isVisible() and dialog.rect().contains(rectangle)
    assert dialog.criteria.horizontalScrollBar().maximum() == 0


def test_held_checkbox_click_survives_live_results_collapsing(historical_card):
    _, _, dialog, app = historical_card
    type_query(dialog.topic.editor, "защита")
    app.processEvents()
    assert dialog.topic.results.isVisible() and dialog.written.isChecked()
    target = dialog.written
    point = QPoint(8, target.height() // 2)
    window_point = target.mapTo(dialog, point)
    # QWindow input updates QApplication.mouseButtons, matching a native pointer.
    # The QWidget overload only sends events and does not maintain that state.
    QTest.mousePress(dialog.windowHandle(), Qt.MouseButton.LeftButton, pos=window_point)
    assert QApplication.mouseButtons() == Qt.MouseButton.LeftButton
    app.processEvents()
    QTest.mouseRelease(dialog.windowHandle(), Qt.MouseButton.LeftButton, pos=window_point)
    QTest.qWait(60)
    app.processEvents()
    assert not dialog.written.isChecked(), (
        "Focus-out reflow must not move the click target before release"
    )
    assert not dialog.topic.results.isVisible()
