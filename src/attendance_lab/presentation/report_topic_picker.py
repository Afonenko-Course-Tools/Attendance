"""Live report topic search using the same search/list controls as card binding."""

from collections.abc import Iterable
from typing import Protocol

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListView,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.text_search import TextSearch
from attendance_lab.presentation.search_results import SearchResultsPane


class TopicEntry(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def section(self) -> str: ...

    @property
    def source_number(self) -> int | None: ...


class ReportTopicPicker(QWidget):
    def __init__(self, entries: Iterable[TopicEntry], parent=None):
        super().__init__(parent)
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.timeout.connect(self._focus_left)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._resize_results)
        entries = tuple(entries)
        positions = {id(entry): index for index, entry in enumerate(entries)}
        self.index = TextSearch(
            entries,
            text=lambda entry: f"{entry.title} {entry.section} {entry.source_number or ''}",
            order=lambda entry: positions[id(entry)],
        )
        self._searching = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search_pane = SearchResultsPane(self)
        self.editor = self.search_pane.search
        self.editor.setPlaceholderText(
            "Название, раздел или номер · ↓ к результатам · можно свою тему"
        )
        self.editor.setClearButtonEnabled(True)
        self.results = self.search_pane.results
        self.results.setWordWrap(True)
        self.results.setSpacing(4)
        self.results.setResizeMode(QListView.ResizeMode.Adjust)
        self.results.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.hide()
        layout.addWidget(self.search_pane)
        self.notice = QLabel("Нет совпадений. Можно сохранить своё название темы.")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview.hide()
        layout.addWidget(self.preview)
        self.editor.textChanged.connect(self._update_preview)
        self.editor.textEdited.connect(self._search)
        self.editor.installEventFilter(self)
        self.results.installEventFilter(self)
        self.results.itemClicked.connect(lambda _: self._choose())
        self.search_pane.activation.connect(self._choose)

    def _search(self, text):
        self._searching = True
        self.results.clear()
        for entry in self.index.find(text):
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
        self.results.setVisible(bool(self.results.count()))
        self.notice.setVisible(not self.results.count())
        self._update_preview()
        self._resize_results()
        # Settle the form before the next pointer event can target a moved field.
        widget: QWidget | None = self.search_pane
        while widget is not None:
            layout = widget.layout()
            if layout is not None:
                layout.activate()
            if widget.isWindow():
                break
            widget = widget.parentWidget()

    def _resize_results(self):
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self._resize_timer.start(25)
            return
        if self._searching and self.results.count():
            rows = min(4, self.results.count())
            height = sum(self.results.sizeHintForRow(row) + 8 for row in range(rows))
            target = min(200, max(60, height + 2 * self.results.frameWidth()))
            if self.results.minimumHeight() != target or self.results.maximumHeight() != target:
                self.results.setFixedHeight(target)

    def _update_preview(self, *_):
        text = self.currentText()
        self.preview.setText(text)
        self.preview.setVisible(
            not self._searching
            and bool(text)
            and self.editor.fontMetrics().horizontalAdvance(text) > self.editor.width() - 24
        )

    def close_results(self):
        self._searching = False
        self.results.clearSelection()
        self.results.setCurrentRow(-1)
        self.results.hide()
        self.notice.hide()
        self._update_preview()

    def _choose(self):
        item = self.results.currentItem()
        if item is None or not item.isSelected() or item.isHidden():
            return
        self.setCurrentText(item.data(Qt.ItemDataRole.UserRole))
        self.editor.setFocus()

    def _focus_left(self):
        # Keep the target in place until its mouse release completes the click.
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self._focus_timer.start(25)
            return
        focused = QApplication.focusWidget()
        if focused is None or not self.isAncestorOf(focused):
            self.close_results()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusOut:
            self._focus_timer.start(0)
        elif event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if watched is self.editor and key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if not self._searching:
                    self._search(self.currentText())
            elif key == Qt.Key.Key_Escape and self._searching:
                self.close_results()
                self.editor.setFocus()
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview()
        self._resize_timer.start(0)

    def currentText(self):
        return self.editor.text()

    def setCurrentText(self, text):
        self.editor.setText(text)
        self.close_results()

    def setEditText(self, text):
        self.setCurrentText(text)

    def lineEdit(self):
        return self.editor
