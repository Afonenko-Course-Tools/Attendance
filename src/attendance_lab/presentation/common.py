"""Small Qt helpers shared by private views."""

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from attendance_lab.domain.models import Lesson

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


def actions(*items: tuple[str, Callable[[], object]]) -> QHBoxLayout:
    layout = QHBoxLayout()
    for text, callback in items:
        button = QPushButton(text)
        button.clicked.connect(lambda checked=False, call=callback: call())
        layout.addWidget(button)
    layout.addStretch()
    return layout


def table(headers: Sequence[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def fill_table(
    widget: QTableWidget, rows: Iterable[Sequence[object]], keys: Iterable[object]
) -> None:
    selected = selected_key(widget)
    records = list(rows)
    row_keys = list(keys)
    widget.setSortingEnabled(False)
    widget.setRowCount(len(records))
    for row_index, (values, key) in enumerate(zip(records, row_keys, strict=True)):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setData(Qt.ItemDataRole.UserRole, key)
            widget.setItem(row_index, column, item)
        if key == selected:
            widget.selectRow(row_index)
    widget.resizeColumnsToContents()


def selected_key(widget: QTableWidget) -> object | None:
    row = widget.currentRow()
    item = widget.item(row, 0) if row >= 0 else None
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
QWidget { font-size: 13px; }
QLabel#Heading { font-size: 25px; font-weight: 700; color: #17243c; }
QLabel#Muted { color: #62718a; }
QLabel#ActiveBanner { background: #e6f2ec; color: #164c36; padding: 12px; border-radius: 7px; font-weight: 600; }
QTabWidget::pane { background: white; border: 1px solid #dbe2ed; border-radius: 7px; }
QTabBar::tab { padding: 11px 18px; background: #e7ecf4; color: #384b67; }
QTabBar::tab:selected { background: white; color: #1455a0; font-weight: 600; }
QPushButton { background: #fff; color: #234667; border: 1px solid #c8d3e2; border-radius: 5px; padding: 7px 12px; }
QPushButton:hover { background: #e8f0fa; border-color: #90abc9; }
QPushButton:pressed { background: #d8e7f8; }
QPushButton:disabled { color: #9ba6b4; background: #f1f3f6; }
QPushButton#Primary { background: #1768b8; color: white; border-color: #1768b8; font-weight: 600; }
QLineEdit, QComboBox, QSpinBox, QDateEdit, QDateTimeEdit, QTextEdit { background: white; color: #17243c; selection-background-color: #1768b8; selection-color: white; border: 1px solid #cbd5e2; border-radius: 4px; padding: 5px; }
QComboBox QAbstractItemView { background: white; color: #17243c; selection-background-color: #deebfa; selection-color: #142f53; }
QTableWidget, QListWidget { background: white; color: #17243c; alternate-background-color: #f5f8fc; gridline-color: #e8edf4; border: 1px solid #dbe2ed; }
QTableWidget::item:selected, QListWidget::item:selected { background: #deebfa; color: #142f53; }
QHeaderView::section { background: #eef3f9; border: 0; border-bottom: 1px solid #d6e0ed; padding: 8px; font-weight: 600; color: #344b69; }
QGroupBox { border: 1px solid #dbe2ed; border-radius: 6px; margin-top: 14px; padding: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QMenu { background: white; color: #17243c; border: 1px solid #cbd5e2; }
QMenu::item:selected { background: #deebfa; color: #142f53; }
QToolTip { background: #fff9db; color: #17243c; border: 1px solid #cbd5e2; }
"""
