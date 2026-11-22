"""Search and check rows while retaining selections across sorts and filters."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from attendance_lab.presentation.common import (
    bind_table_clear,
    fill_table,
    filter_table,
    plain_label,
    search_field,
    table,
)


class ChoiceTable(QWidget):
    selection_changed = Signal()

    def __init__(self, headers, rows=(), selected=(), *, name, placeholder, keys=(), parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = search_field(placeholder)
        layout.addWidget(self.search)
        self.listing = table(headers, name=name, keys=keys, sortable=True)
        self.listing.setMinimumHeight(135)
        self.listing.setToolTip("Delete или Backspace снимает галочки с выделенных строк.")
        bind_table_clear(self.listing, self.clear_selected_checks)
        layout.addWidget(self.listing, 1)
        buttons = QHBoxLayout()
        for label, state in (
            ("Выбрать видимых", Qt.CheckState.Checked),
            ("Снять выбор видимых", Qt.CheckState.Unchecked),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _, value=state: self.set_visible_checks(value))
            buttons.addWidget(button)
        self.count = plain_label()
        buttons.addWidget(self.count, 1)
        layout.addLayout(buttons)
        self.search.textChanged.connect(self.filter)
        self.listing.model().layoutChanged.connect(self.filter)
        self.listing.itemChanged.connect(self.count_selection)
        self.replace_rows(rows, selected)

    def items(self):
        for row in range(self.listing.rowCount()):
            item = self.listing.item(row, 0)
            if item is not None:
                yield item

    def replace_rows(self, rows, selected=()):
        records = list(rows)
        self.listing.blockSignals(True)
        fill_table(self.listing, [values for _, values in records], [key for key, _ in records])
        self.listing.blockSignals(False)
        self.set_checked(selected)
        self.filter()

    def set_checked(self, selected):
        keys = set(selected)
        self.listing.blockSignals(True)
        for item in self.items():
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) in keys
                else Qt.CheckState.Unchecked
            )
        self.listing.blockSignals(False)
        self.count_selection()

    def selected_ids(self):
        return {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.items()
            if item.checkState() == Qt.CheckState.Checked
        }

    def set_visible_checks(self, state):
        self.set_item_checks(
            (item for item in self.items() if not self.listing.isRowHidden(item.row())), state
        )

    def clear_selected_checks(self):
        self.set_item_checks(
            (
                item
                for item in self.items()
                if item.isSelected() and not self.listing.isRowHidden(item.row())
            ),
            Qt.CheckState.Unchecked,
        )

    def set_item_checks(self, items, state):
        self.listing.blockSignals(True)
        for item in items:
            item.setCheckState(state)
        self.listing.blockSignals(False)
        self.count_selection()

    def filter(self, *_):
        filter_table(self.listing, self.search.text())

    def count_selection(self, *_):
        self.count.setText(f"Выбрано: {len(self.selected_ids())}")
        self.selection_changed.emit()


def group_rows(groups):
    return [(g.id, (g.specialty_name, str(g.number), g.course, g.variant_key)) for g in groups]


def group_table(groups=(), selected=()):
    return ChoiceTable(
        ("Специальность", "Группа", "Курс", "Вариант группы"),
        group_rows(groups),
        selected,
        name="group-picker",
        keys=("specialty", "group", "year", "variant"),
        placeholder="Группа, специальность или курс",
    )


def student_context_table(students, selected=()):
    return ChoiceTable(
        ("ФИО", "Группа", "№ зачётки", "Специальность"),
        [
            (
                s.student_id,
                (s.full_name, s.group_number or "", s.record_book_number or "", s.specialty or ""),
            )
            for s in students
        ],
        selected,
        name="student-picker",
        keys=("student", "group", "record", "specialty"),
        placeholder="ФИО, группа, специальность или № зачётки",
    )
