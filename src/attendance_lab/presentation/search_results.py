"""Shared search/list keyboard interaction without domain or persistence logic."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QListWidget, QVBoxLayout, QWidget


class SearchResultsPane(QWidget):
    activation = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.results = QListWidget()
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.search)
        layout.addWidget(self.results, 1)
        self.search.installEventFilter(self)
        self.results.installEventFilter(self)
        self.results.itemDoubleClicked.connect(lambda *_: self._activate())

    def focus_matches(self, *, last=False):
        indices = range(self.results.count() - 1, -1, -1) if last else range(self.results.count())
        for index in indices:
            item = self.results.item(index)
            if not item.isHidden():
                self.results.setCurrentItem(item)
                self.results.scrollToItem(item)
                self.results.setFocus()
                return

    def focus_search(self):
        self.search.setFocus()

    def focussearch(self):
        self.focus_search()

    def _activate(self):
        item = self.results.currentItem()
        if (
            self.results.isEnabled()
            and item is not None
            and not item.isHidden()
            and item.isSelected()
        ):
            self.activation.emit()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if watched is self.search and key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.focus_matches(last=key == Qt.Key.Key_Up)
                return True
            if key in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
                self._activate()
                return True
        return super().eventFilter(watched, event)
