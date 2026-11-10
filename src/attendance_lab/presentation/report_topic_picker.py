"""Explicit topic selection with full readable titles and shared fuzzy search."""

from collections.abc import Iterable
from typing import Protocol

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.text_search import TextSearch


class TopicEntry(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def section(self) -> str: ...

    @property
    def source_number(self) -> int | None: ...


class ReportTopicSearchDialog(QDialog):
    def __init__(self, entries: Iterable[TopicEntry], query="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Найти тему реферата")
        self.entries = tuple(entries)
        self.selected_title = None
        positions = {id(entry): index for index, entry in enumerate(self.entries)}
        self.index = TextSearch(
            self.entries,
            text=lambda entry: f"{entry.title} {entry.section} {entry.source_number or ''}",
            order=lambda entry: positions[id(entry)],
        )
        layout = QVBoxLayout(self)
        self.search = QLineEdit(query)
        self.search.setPlaceholderText("Название, раздел или номер; можно с опечаткой")
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.setSpacing(5)
        self.results.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.results, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.choose = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        assert self.choose is not None
        cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        assert cancel is not None
        cancel.setText("Отмена")
        self.choose.setText("Выбрать тему")
        self.choose.setAutoDefault(False)
        self.choose.setDefault(False)
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept_selection)
        self.buttons.rejected.connect(self.reject)
        self.results.itemDoubleClicked.connect(lambda _: self.accept_selection())
        self.results.currentItemChanged.connect(self.update_selection)
        self.search.textChanged.connect(self.refresh)
        self.search.installEventFilter(self)
        self.results.installEventFilter(self)
        available = self.screen().availableGeometry()
        self.setMaximumSize(available.size())
        self.resize(min(850, available.width()), min(600, available.height()))
        self.refresh()

    def refresh(self, *_):
        self.results.clear()
        for entry in self.index.find(self.search.text()):
            metadata = " · ".join(
                part
                for part in (
                    f"№ {entry.source_number}" if entry.source_number is not None else "",
                    entry.section,
                )
                if part
            )
            item = QListWidgetItem(f"{entry.title}\n{metadata}" if metadata else entry.title)
            item.setData(Qt.ItemDataRole.UserRole, entry.title)
            item.setToolTip(item.text())
            self.results.addItem(item)
        self.results.setCurrentRow(-1)
        self.update_selection()

    def update_selection(self, *_):
        self.choose.setEnabled(self.results.currentItem() is not None)

    def accept_selection(self):
        item = self.results.currentItem()
        if item is not None:
            self.selected_title = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if watched is self.search and key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self.results.count():
                    self.results.setCurrentRow(
                        0 if key == Qt.Key.Key_Down else self.results.count() - 1
                    )
                    self.results.setFocus()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.accept_selection()
                return True
        return super().eventFilter(watched, event)


class ReportTopicPicker(QWidget):
    def __init__(self, entries: Iterable[TopicEntry], parent=None):
        super().__init__(parent)
        self.entries = tuple(entries)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.editor = QLineEdit()
        self.editor.setPlaceholderText("Выберите тему или введите свою")
        self.search_button = QPushButton("Найти тему…")
        self.search_button.setAutoDefault(False)
        row.addWidget(self.editor, 1)
        row.addWidget(self.search_button)
        layout.addLayout(row)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.preview)
        self.editor.textChanged.connect(self.preview.setText)
        self.editor.installEventFilter(self)
        self.search_button.clicked.connect(self.open_search)

    def eventFilter(self, watched, event):
        if (
            watched is self.editor
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self.open_search()
            return True
        return super().eventFilter(watched, event)

    def currentText(self):
        return self.editor.text()

    def setCurrentText(self, text):
        self.editor.setText(text)

    def setEditText(self, text):
        self.setCurrentText(text)

    def lineEdit(self):
        return self.editor

    def open_search(self):
        dialog = ReportTopicSearchDialog(self.entries, query=self.currentText(), parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_title is not None:
            self.setCurrentText(dialog.selected_title)
