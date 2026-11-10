"""Topic search is explicit, readable and independent of attendance storage."""

from dataclasses import dataclass

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from attendance_lab.presentation.report_topic_picker import (
    ReportTopicPicker,
    ReportTopicSearchDialog,
)


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
    yield app, entries


def test_fuzzy_search_has_no_implicit_choice_and_keyboard_selects_title(topics):
    app, entries = topics
    dialog = ReportTopicSearchDialog(entries)
    dialog.show()
    app.processEvents()
    assert dialog.results.count() == 3
    assert dialog.results.item(0).data(Qt.ItemDataRole.UserRole) == entries[0].title
    assert not dialog.choose.isEnabled()
    dialog.search.setText("данных зашита")
    assert dialog.results.count() == 1
    QTest.keyClick(dialog.search, Qt.Key.Key_Return)
    assert dialog.isVisible() and dialog.selected_title is None
    QTest.keyClick(dialog.search, Qt.Key.Key_Down)
    assert dialog.choose.isEnabled()
    QTest.keyClick(dialog.results, Qt.Key.Key_Return)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_title == entries[0].title
    dialog.close()


def test_full_long_title_wraps_and_empty_search_disables_choice(topics):
    app, entries = topics
    dialog = ReportTopicSearchDialog(entries, query="архитектуры")
    dialog.show()
    app.processEvents()
    item = dialog.results.item(0)
    assert entries[2].title in item.text()
    assert "№ 3" in item.text()
    assert dialog.results.visualItemRect(item).height() > dialog.fontMetrics().height() * 2
    available = dialog.screen().availableGeometry()
    assert dialog.width() <= available.width() and dialog.height() <= available.height()
    dialog.search.setText("абсолютнонесуществующее")
    assert dialog.results.count() == 0 and not dialog.choose.isEnabled()
    QTest.keyClick(dialog.search, Qt.Key.Key_Escape)
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.close()


def test_custom_text_and_cancel_do_not_change_picker(topics, monkeypatch):
    _, entries = topics
    picker = ReportTopicPicker(entries)
    picker.setEditText("Своя согласованная тема")
    assert picker.currentText() == picker.lineEdit().text() == picker.preview.text()
    monkeypatch.setattr(ReportTopicSearchDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    QTest.keyClick(picker.lineEdit(), Qt.Key.Key_Return)
    assert picker.currentText() == "Своя согласованная тема"

    def choose(dialog):
        dialog.selected_title = entries[1].title
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ReportTopicSearchDialog, "exec", choose)
    picker.search_button.click()
    assert picker.currentText() == entries[1].title
    picker.close()


def test_double_click_accepts_exact_title_without_metadata(topics):
    app, entries = topics
    dialog = ReportTopicSearchDialog(entries, query="мониторинг")
    dialog.show()
    app.processEvents()
    item = dialog.results.item(0)
    position = dialog.results.visualItemRect(item).center()
    QTest.mouseClick(dialog.results.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(dialog.results.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_title == entries[1].title
    dialog.close()
