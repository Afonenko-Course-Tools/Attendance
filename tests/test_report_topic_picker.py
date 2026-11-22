"""Live topic selection shares binding controls and never saves its parent card."""

from dataclasses import dataclass

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout

from attendance_lab.presentation.report_topic_picker import ReportTopicPicker
from attendance_lab.presentation.search_results import SearchResultsPane


@dataclass(frozen=True)
class Entry:
    title: str
    section: str = ""
    source_number: int | None = None


@pytest.fixture
def topics():
    app = QApplication.instance() or QApplication([])
    entries = (
        Entry("Защита персональных данных", "Регулирование", 1),
        Entry("Учёт сетевых соединений", "Мониторинг", 2),
        Entry("Очень длинная тема " * 30, "Архитектуры", 3),
    )
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    picker = ReportTopicPicker(entries)
    layout.addWidget(picker)
    save = QPushButton("Сохранить")
    save.setDefault(True)
    save.clicked.connect(dialog.accept)
    layout.addWidget(save)
    dialog.resize(640, 400)
    dialog.show()
    dialog.activateWindow()
    picker.editor.setFocus()
    app.processEvents()
    yield app, entries, dialog, picker, save
    dialog.close()


def type_query(picker, text):
    picker.editor.selectAll()
    QTest.keyClick(picker.editor, Qt.Key.Key_Backspace)
    # QTest.keyClicks only handles ASCII; a paste follows the same textEdited path.
    QApplication.clipboard().setText(text)
    QTest.keyClick(picker.editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)


def test_typing_filters_immediately_and_explicit_keyboard_selection_keeps_card_open(topics):
    app, entries, dialog, picker, _ = topics
    assert isinstance(picker.search_pane, SearchResultsPane)
    assert not picker.findChildren(QPushButton)
    type_query(picker, "данных зашита")
    app.processEvents()
    assert picker.results.isVisible() and picker.results.count() == 1
    assert picker.currentText() == "данных зашита"
    assert picker.results.currentItem() is None
    QTest.keyClick(picker.editor, Qt.Key.Key_Return)
    assert dialog.isVisible() and picker.currentText() == "данных зашита"
    QTest.keyClick(picker.editor, Qt.Key.Key_Down)
    QTest.keyClick(picker.results, Qt.Key.Key_Return)
    assert picker.currentText() == entries[0].title
    assert not picker.results.isVisible() and dialog.isVisible()


def test_down_browses_and_single_click_selects_exact_title(topics):
    app, entries, dialog, picker, _ = topics
    QTest.keyClick(picker.editor, Qt.Key.Key_Down)
    app.processEvents()
    assert picker.results.count() == 3
    assert picker.results.item(0).isSelected()
    item = picker.results.item(1)
    QTest.mouseClick(
        picker.results.viewport(),
        Qt.MouseButton.LeftButton,
        pos=picker.results.visualItemRect(item).center(),
    )
    assert picker.currentText() == entries[1].title
    assert dialog.isVisible() and not picker.results.isVisible()


def test_changed_query_clears_stale_selection_and_escape_only_closes_results(topics):
    app, _, dialog, picker, _ = topics
    type_query(picker, "данных")
    QTest.keyClick(picker.editor, Qt.Key.Key_Down)
    picker.editor.setFocus()
    type_query(picker, "совсемнесуществующее")
    app.processEvents()
    assert picker.results.count() == 0 and picker.notice.isVisible()
    QTest.keyClick(picker.editor, Qt.Key.Key_Return)
    assert dialog.isVisible()
    QTest.keyClick(picker.editor, Qt.Key.Key_Escape)
    assert dialog.isVisible() and not picker.notice.isVisible()
    assert picker.currentText() == "совсемнесуществующее"
    QTest.keyClick(picker.editor, Qt.Key.Key_Escape)
    assert dialog.result() == QDialog.DialogCode.Rejected and not dialog.isVisible()


def test_custom_title_and_save_need_only_one_click(topics):
    app, _, dialog, picker, save = topics
    type_query(picker, "Своя согласованная тема")
    app.processEvents()
    QTest.mouseClick(save, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert picker.currentText() == "Своя согласованная тема"
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_long_results_wrap_and_draft_can_leave_search_without_selection(topics):
    app, entries, dialog, picker, save = topics
    type_query(picker, "архитектуры")
    app.processEvents()
    item = picker.results.item(0)
    assert entries[2].title in item.text() and "№ 3" in item.text()
    assert picker.results.visualItemRect(item).height() > picker.fontMetrics().height() * 2
    assert picker.results.height() <= 200
    save.setFocus()
    app.processEvents()
    assert not picker.results.isVisible()
    assert picker.currentText() == "архитектуры" and dialog.isVisible()
