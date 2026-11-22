"""Small Qt helpers shared by private views."""

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, Qt, Slot
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from attendance_lab.application.text_search import TextSearch, normalized
from attendance_lab.domain.models import Lesson
from attendance_lab.presentation.interface_zoom import InterfaceZoom
from attendance_lab.presentation.table_columns import TableColumns

STATES = {"planned": "Запланировано", "open": "Регистрация открыта", "completed": "Завершено"}


def plain_label(text: str = "", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    return label


def private_message(
    parent: QWidget | None, title: str, text: str, *, question: bool = False
) -> bool:
    box = QMessageBox(parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Question if question else QMessageBox.Icon.Information)
    if question:
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
    else:
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
    return box.exec() == QMessageBox.StandardButton.Yes


def guarded(parent: QWidget, operation: Callable[[], object]) -> object | None:
    try:
        return operation()
    except Exception as exc:
        private_message(parent, "Не удалось выполнить действие", str(exc))
        return None


class _TableClearKeys(QObject):
    def __init__(self, view: QAbstractItemView, action: Callable[[], object]):
        super().__init__(view)
        self.view = view
        self.action = action
        view.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            watched is self.view
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
            and event.modifiers()
            in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)
            and self.view.state() != QAbstractItemView.State.EditingState
        ):
            if not event.isAutoRepeat():
                guarded(self.view, self.action)
            return True
        return super().eventFilter(watched, event)


def bind_table_clear(view: QAbstractItemView, action: Callable[[], object]) -> None:
    """Apply a table's own clear action when the view receives an unmodified key."""
    _TableClearKeys(view, action)


def actions(*items: tuple[str, Callable[[], object]]) -> QHBoxLayout:
    layout = QHBoxLayout()
    for text, callback in items:
        button = QPushButton(text)
        button.clicked.connect(lambda checked=False, call=callback: call())
        layout.addWidget(button)
    layout.addStretch()
    return layout


def search_field(placeholder: str, *, name: str = "Поиск") -> QLineEdit:
    field = QLineEdit()
    field.setAccessibleName(name)
    field.setPlaceholderText(placeholder)
    field.setClearButtonEnabled(True)
    field.setMinimumWidth(160)
    return field


@dataclass(frozen=True)
class TableValue:
    text: str
    order: Any


class TableCell(QTableWidgetItem):
    def __init__(self, value):
        display = value.text if isinstance(value, TableValue) else str(value)
        super().__init__(display)
        order = value.order if isinstance(value, TableValue) else value
        self.order = (
            tuple(
                (0, int(part)) if part.isdecimal() else (1, part)
                for part in re.split(r"(\d+)", normalized(order))
            )
            if isinstance(order, str)
            else order
        )

    def __lt__(self, other):
        if isinstance(other, TableCell) and type(self.order) is type(other.order):
            return self.order < other.order
        return super().__lt__(other)


def table(headers: Sequence[str], *, name: str = "", keys=(), sortable=False) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setStretchLastSection(True)
    if name:
        columns = TableColumns(widget, name, keys=keys)
        columns.restore_visibility()
    if sortable:
        widget.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        widget.setSortingEnabled(True)
    return widget


def fill_table(
    widget: QTableWidget,
    rows: Iterable[Sequence[object]],
    keys: Iterable[object],
    *,
    restore_sort=True,
) -> None:
    selected = selected_key(widget)
    by_cell = widget.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectItems
    selected_cells = set()
    for index in widget.selectedIndexes():
        identity = widget.item(index.row(), 0)
        if identity is not None:
            selected_cells.add((identity.data(Qt.ItemDataRole.UserRole), index.column()))
    current_column = widget.currentColumn()
    records = list(rows)
    row_keys = list(keys)
    sorting = widget.isSortingEnabled()
    widget.setSortingEnabled(False)
    widget.clearSelection()
    widget.setCurrentCell(-1, -1)
    widget.setRowCount(len(records))
    for row_index, (values, key) in enumerate(zip(records, row_keys, strict=True)):
        for column, value in enumerate(values):
            item = TableCell(value)
            item.setData(Qt.ItemDataRole.UserRole, key)
            header = widget.horizontalHeaderItem(column)
            if header is not None and header.text() in ("Группа", "Группа / состав"):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            widget.setItem(row_index, column, item)
        if by_cell:
            for column in range(len(values)):
                if (key, column) in selected_cells:
                    item = widget.item(row_index, column)
                    assert item is not None
                    item.setSelected(True)
                    if key == selected and column == current_column:
                        widget.setCurrentCell(
                            row_index, column, QItemSelectionModel.SelectionFlag.NoUpdate
                        )
        elif key == selected:
            widget.selectRow(row_index)
    if restore_sort and sorting:
        widget.setSortingEnabled(True)
    columns = widget.findChild(TableColumns)
    if columns is not None:
        columns.schedule_fit()
    else:
        widget.resizeColumnsToContents()


def filter_table(widget: QTableWidget, query: str, *, columns: Sequence[int] | None = None) -> int:
    columns = range(widget.columnCount()) if columns is None else columns
    rows = range(widget.rowCount())
    matched = (
        set(
            TextSearch(
                rows,
                text=lambda row: " ".join(
                    item.text()
                    for column in columns
                    if (item := widget.item(row, column)) is not None
                ),
            ).find(query)
        )
        if query.strip()
        else set(rows)
    )
    for row in rows:
        widget.setRowHidden(row, row not in matched)
    preferences = widget.findChild(TableColumns)
    if preferences is not None:
        preferences.schedule_fit()
    return len(matched)


def selected_key(widget: QTableWidget) -> object | None:
    selected = [index for index in widget.selectedIndexes() if not widget.isRowHidden(index.row())]
    if not selected:
        return None
    current = widget.currentRow()
    row = current if any(index.row() == current for index in selected) else selected[0].row()
    item = widget.item(row, 0)
    return item.data(Qt.ItemDataRole.UserRole) if item else None


def local_time(at: datetime, timezone: str | None = None) -> str:
    value = at.astimezone(ZoneInfo(timezone)) if timezone else at.astimezone()
    return value.strftime("%d.%m.%Y %H:%M:%S %Z")


def lesson_label(lesson: Lesson, discipline_name: str = "") -> str:
    start = lesson.starts_at.astimezone(ZoneInfo(lesson.timezone))
    end = lesson.ends_at.astimezone(ZoneInfo(lesson.timezone))
    end_label = end.strftime("%d.%m.%Y %H:%M" if end.date() != start.date() else "%H:%M")
    return " · ".join(
        str(part)
        for part in (
            discipline_name,
            lesson.title,
            f"{start:%d.%m.%Y %H:%M}–{end_label} {lesson.timezone}",
            lesson.room,
        )
        if part
    )


STYLE = """
QMainWindow, QDialog { background: #f3f6fa; color: #17243c; }
QWidget { font-size: 16px; }
QLabel#Heading { font-size: 23px; font-weight: 700; color: #17243c; }
QLabel#Muted { color: #62718a; }
QLabel#ActiveBanner { background: #e6f2ec; color: #164c36; padding: 7px; border-radius: 4px; font-weight: 600; }
QTabWidget::pane { background: white; border: 1px solid #dbe2ed; border-radius: 7px; }
QTabBar::tab { padding: 9px 17px; background: #e7ecf4; color: #384b67; }
QTabBar::tab:selected { background: white; color: #1455a0; font-weight: 600; }
QPushButton { background: #fff; color: #234667; border: 1px solid #c8d3e2; border-radius: 5px; padding: 8px 13px; }
QPushButton:enabled:hover { background: #e8f0fa; border-color: #90abc9; }
QPushButton:enabled:pressed { background: #d8e7f8; }
QPushButton:disabled { color: #9ba6b4; background: #f1f3f6; }
QPushButton#Primary:enabled { background: #1768b8; color: white; border-color: #1768b8; font-weight: 600; }
QLineEdit, QComboBox, QSpinBox, QDateEdit, QDateTimeEdit, QTextEdit { background: white; color: #17243c; selection-background-color: #1768b8; selection-color: white; border: 1px solid #cbd5e2; border-radius: 4px; padding: 6px; }
QComboBox QAbstractItemView { background: white; color: #17243c; selection-background-color: #deebfa; selection-color: #142f53; }
QTableWidget, QListWidget { background: white; color: #17243c; alternate-background-color: #f5f8fc; gridline-color: #e8edf4; border: 1px solid #dbe2ed; }
QTableWidget::item:selected, QListWidget::item:selected { background: #deebfa; color: #142f53; }
QHeaderView::section { background: #eef3f9; border: 0; border-bottom: 1px solid #d6e0ed; padding: 8px; font-weight: 600; color: #344b69; }
QGroupBox { border: 1px solid #dbe2ed; border-radius: 6px; margin-top: 14px; padding: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QToolBar { background: transparent; border: 0; spacing: 4px; padding: 2px 0; }
QToolButton { border: 1px solid transparent; border-radius: 3px; padding: 6px 9px; color: #344054; }
QToolButton:enabled:hover { background: #eef2f6; border-color: #d0d5dd; }
QToolButton:enabled:pressed, QToolButton:checked { background: #e4eaf1; border-color: #bcc8d7; }
QToolButton:disabled { color: #98a2b3; background: transparent; border-color: transparent; }
QMenuBar, QMenu { font-size: 18px; }
QMenu { background: white; color: #17243c; border: 1px solid #cbd5e2; }
QMenu::item:selected { background: #deebfa; color: #142f53; }
QToolTip { background: #fff9db; color: #17243c; border: 1px solid #cbd5e2; }
"""


class WidgetZoom(QObject):
    def __init__(self, window: QWidget, zoom: InterfaceZoom):
        super().__init__(window)
        self.window, self.zoom = window, zoom
        zoom.changed.connect(self.apply)
        self.apply()
        for sequences, callback in (
            (("Ctrl++", "Ctrl+=", "Ctrl+Shift+="), zoom.zoomIn),
            (("Ctrl+-",), zoom.zoomOut),
            (("Ctrl+0",), zoom.reset),
        ):
            shortcut = QShortcut(window)
            shortcut.setKeys([QKeySequence(sequence) for sequence in sequences])
            shortcut.activated.connect(callback)

    @Slot()
    def apply(self):
        style = re.sub(
            r"(\d+)px",
            lambda size: f"{max(1, self.zoom.scale_pixels(int(size[1])))}px",
            STYLE,
        )
        self.window.setStyleSheet(style)
