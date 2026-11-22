"""Search and choose an actual lesson in a bounded window."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
)

from attendance_lab.application.text_search import TextSearch
from attendance_lab.presentation.common import (
    TableValue,
    fill_table,
    plain_label,
    search_field,
    table,
)

LESSON_STATUS = {
    "planned": "Запланировано",
    "open": "Регистрация открыта",
    "closed": "Завершено",
    "completed": "Завершено",
    "deleted": "Удалено",
}
IDENTITY = Qt.ItemDataRole.UserRole


@dataclass(frozen=True)
class LessonPickerOption[K]:
    id: K
    date: date
    time: str
    topic: str
    group: str
    status: str = ""
    label: str = ""


class _LessonChoice[K](Protocol):
    @property
    def id(self) -> K: ...
    @property
    def date(self) -> date: ...
    @property
    def time(self) -> str: ...
    @property
    def topic(self) -> str: ...
    @property
    def group(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def label(self) -> str: ...


def _caption(lesson: _LessonChoice[object]) -> str:
    return lesson.label or f"{lesson.date:%d.%m.%Y} {lesson.time} · {lesson.topic} · {lesson.group}"


class LessonPickerDialog[K](QDialog):
    def __init__(
        self,
        choices: Iterable[_LessonChoice[K]],
        *,
        selected_id: K | None = None,
        allow_all=False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Выберите занятие")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.lesson_id: K | None = selected_id
        self.choices = tuple(choices)
        self.index = TextSearch(
            self.choices,
            text=lambda lesson: " ".join(
                (
                    _caption(lesson),
                    lesson.date.isoformat(),
                    lesson.topic,
                    lesson.group,
                    LESSON_STATUS.get(lesson.status, lesson.status),
                )
            ),
            order=lambda lesson: (lesson.date, lesson.time),
        )

        layout = QVBoxLayout(self)
        self.search = search_field("Дата, время, тема или группа · F3", name="Поиск занятия")
        layout.addWidget(self.search)
        self.count = plain_label()
        layout.addWidget(self.count)
        self.lessons = table(
            ["Дата", "Время", "Тема", "Группа / состав", "Статус"],
            name="lesson-picker",
            keys=("date", "time", "topic", "group", "status"),
            sortable=True,
        )
        self.lessons.setAccessibleName("Результаты поиска занятий")
        layout.addWidget(self.lessons, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.choose = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.choose.setText("Выбрать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        if allow_all:
            self.all_button = buttons.addButton(
                "Все занятия", QDialogButtonBox.ButtonRole.ResetRole
            )
            self.all_button.clicked.connect(self.accept_all)
        layout.addWidget(buttons)

        self.lessons.currentItemChanged.connect(self.update_actions)
        self.lessons.itemDoubleClicked.connect(self.accept_selection)
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(self.accept_selection)
        self.search_shortcut = QShortcut(QKeySequence("F3"), self)
        self.search_shortcut.activated.connect(self.focus_search)
        self.results_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Down), self.search)
        self.results_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.results_shortcut.activated.connect(self.focus_results)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            choose_shortcut = QShortcut(QKeySequence(key), self.lessons)
            choose_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            choose_shortcut.activated.connect(self.accept_selection)
        self.filter()
        self.focus_search()

        available = self.screen().availableGeometry()
        width = min(780, max(1, available.width() - 48))
        if parent is not None:
            width = min(width, max(500, parent.window().width()))
        self.resize(width, min(470, max(1, available.height() - 72)))

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def focus_results(self):
        if self.lessons.rowCount():
            self.lessons.setFocus()

    def filter(self, *_):
        current = self.lessons.currentItem()
        selected = current.data(IDENTITY) if current else self.lesson_id
        matches = self.index.find(self.search.text())
        rows = [
            (
                TableValue(f"{lesson.date:%d.%m.%Y}", (lesson.date, lesson.time)),
                lesson.time,
                lesson.topic,
                lesson.group,
                LESSON_STATUS.get(lesson.status, lesson.status),
            )
            for lesson in matches
        ]
        fill_table(self.lessons, rows, (lesson.id for lesson in matches))
        if matches:
            selected_row = 0
            for row in range(len(matches)):
                for column in range(self.lessons.columnCount()):
                    item = self.lessons.item(row, column)
                    assert item is not None
                    item.setToolTip(item.text())
                    if column == 0 and item.data(IDENTITY) == selected:
                        selected_row = row
            self.lessons.setCurrentCell(selected_row, 0)
        current = self.lessons.currentItem()
        if current is not None:
            self.lessons.scrollToItem(current)
        self.count.setText(f"Показано {len(matches)} из {len(self.choices)}")
        self.update_actions()

    def update_actions(self, *_):
        self.choose.setEnabled(self.lessons.currentItem() is not None)

    def accept_selection(self, *_):
        item = self.lessons.currentItem()
        if item is not None:
            self.lesson_id = item.data(IDENTITY)
            self.accept()

    def accept_all(self):
        self.lesson_id = None
        self.accept()


class LessonPicker[K](QPushButton):
    """A compact value control shared by filters and editable lesson fields."""

    currentIndexChanged = Signal(int)

    def __init__(self, parent=None, *, allow_all=False):
        super().__init__(parent)
        self.allow_all = allow_all
        self.choices: tuple[_LessonChoice[K], ...] = ()
        self._selected: _LessonChoice[K] | None = None
        self.setAccessibleName("Занятие")
        self.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(140)
        self.clicked.connect(self.open_picker)
        self.update_caption()

    def replace_choices(
        self,
        choices: Iterable[_LessonChoice[K]],
        selected_id: K | None = None,
        *,
        select_first=False,
    ):
        self.choices = tuple(choices)
        index = self.findData(selected_id)
        if index < 0 and select_first and self.choices:
            index = 0
        self.setCurrentIndex(index)

    def currentData(self) -> K | None:
        return self._selected.id if self._selected is not None else None

    def currentIndex(self) -> int:
        return self.findData(self.currentData())

    def currentText(self) -> str:
        if self._selected is not None:
            return _caption(self._selected)
        return "Все занятия" if self.allow_all else "Выберите занятие…"

    def findData(self, lesson_id: K | None) -> int:
        if lesson_id is None:
            return 0 if self.allow_all else -1
        offset = int(self.allow_all)
        return next(
            (i + offset for i, lesson in enumerate(self.choices) if lesson.id == lesson_id), -1
        )

    def setCurrentIndex(self, index: int):
        previous = self.currentData()
        row = index - int(self.allow_all)
        self._selected = self.choices[row] if 0 <= row < len(self.choices) else None
        self.update_caption()
        if self.currentData() != previous:
            self.currentIndexChanged.emit(self.findData(self.currentData()))

    def update_caption(self):
        caption = self.currentText()
        width = max(0, self.contentsRect().width() - self.iconSize().width() - 32)
        self.setText(self.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, width))
        self.setToolTip(caption)
        self.setAccessibleDescription(caption)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.update_caption()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_caption()

    def open_picker(self):
        dialog = LessonPickerDialog(
            self.choices, selected_id=self.currentData(), allow_all=self.allow_all, parent=self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.setCurrentIndex(self.findData(dialog.lesson_id))
        dialog.deleteLater()
