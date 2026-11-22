"""Discipline schedules and explicit expected rosters."""

from collections.abc import Callable

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import LessonCounts
from attendance_lab.presentation.common import (
    STATES,
    TableCell,
    TableValue,
    actions,
    bind_table_clear,
    fill_table,
    filter_table,
    guarded,
    lesson_label,
    plain_label,
    private_message,
    search_field,
    selected_key,
    table,
)
from attendance_lab.presentation.dialogs import (
    DisciplineDialog,
    LessonDialog,
    StudentSelectionDialog,
)
from attendance_lab.presentation.lesson_lifecycle import (
    delete_lesson_dialog,
    restore_lessons_dialog,
)


def _registration_counts(counts: LessonCounts) -> TableValue:
    return TableValue(
        f"Карт: {counts.unique_cards} · Студентов: {counts.identified_students}\n"
        f"Без сопоставления: {counts.unidentified_cards}",
        (counts.unique_cards, counts.identified_students, counts.unidentified_cards),
    )


class SchedulePane(QWidget):
    def __init__(
        self,
        service: AttendanceService,
        changed: Callable[[], None],
        open_lesson: Callable[[int], None],
        view_log: Callable[[int], None],
        default_timezone: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.changed = changed
        self.open_lesson = open_lesson
        self.view_log = view_log
        self.default_timezone = default_timezone
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        top.addWidget(plain_label("Просматриваемая дисциплина"))
        self.discipline = QComboBox()
        self.discipline.setMinimumWidth(260)
        top.addWidget(self.discipline, 1)
        layout.addLayout(top)
        layout.addLayout(
            actions(
                ("Добавить дисциплину", lambda: self.edit_discipline(False)),
                ("Изменить дисциплину", lambda: self.edit_discipline(True)),
                ("Состав дисциплины…", self.discipline_roster),
            )
        )
        self.description = plain_label(wrap=True)
        self.description.setObjectName("Muted")
        self.description.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.description.setMaximumHeight(self.description.fontMetrics().lineSpacing() * 2)
        layout.addWidget(self.description)
        self.lessons = table(
            (
                "ID",
                "Дата и время занятия",
                "Название",
                "Аудитория",
                "Состояние",
                "Ожидаемый состав",
                "Регистрация",
            ),
            name="source-schedule",
            keys=("id", "date", "title", "room", "state", "roster", "registration"),
            sortable=True,
        )
        for column, tooltip in (
            (5, "Заранее назначенные студенты. Этот состав используется для учёта пропусков."),
            (
                6,
                "Уникальные карты и распознанные студенты на этом занятии. "
                "Повторы не увеличивают счётчики; несколько карт одного студента дают одно присутствие.",
            ),
        ):
            header = self.lessons.horizontalHeaderItem(column)
            if header is not None:
                header.setToolTip(tooltip)
        self.lessons.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addLayout(
            actions(
                ("Добавить занятие", lambda: self.edit_lesson(False)),
                ("Изменить занятие", lambda: self.edit_lesson(True)),
                ("Состав выбранных занятий…", self.lesson_rosters),
                ("Открыть регистрацию", self._open),
            )
        )
        layout.addLayout(
            actions(
                ("Приватный журнал", self._logs),
                ("Удалить выбранное…", self.delete_selected),
                (
                    "Удалённые занятия…",
                    lambda: restore_lessons_dialog(self, self.service, self.changed),
                ),
            )
        )
        self.lessons.setToolTip(
            "Двойной клик — редактировать. Delete или Backspace — запросить удаление одного занятия. "
            "Выбор нескольких занятий — Ctrl или Shift."
        )
        bind_table_clear(self.lessons, self.delete_selected_with_key)
        self.search = search_field(
            "Дата, название, аудитория или состояние · F3", name="Поиск занятия"
        )
        self.search.textChanged.connect(self.filter_rows)
        self.search_shortcut = QShortcut(QKeySequence("F3"), self)
        self.search_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.search_shortcut.activated.connect(self.focus_search)
        layout.addWidget(self.search)
        self.matches = plain_label()
        self.matches.hide()
        layout.addWidget(self.matches)
        self.lessons.itemDoubleClicked.connect(lambda *_: self.edit_lesson(True))
        self.lessons.model().layoutChanged.connect(self.filter_rows)
        layout.addWidget(self.lessons, 1)
        self.discipline.currentIndexChanged.connect(lambda *_: self.refresh_lessons())

    def refresh(self) -> None:
        selected = self.discipline.currentData()
        self.discipline.blockSignals(True)
        self.discipline.clear()
        for discipline in self.service.list_disciplines():
            self.discipline.addItem(discipline.name, discipline.id)
        index = self.discipline.findData(selected)
        self.discipline.setCurrentIndex(
            index if index >= 0 else (0 if self.discipline.count() else -1)
        )
        self.discipline.blockSignals(False)
        self.refresh_lessons()

    def refresh_lessons(self) -> None:
        selected_ids = {
            index.data(Qt.ItemDataRole.UserRole)
            for index in self.lessons.selectionModel().selectedRows()
            if not self.lessons.isRowHidden(index.row())
        }
        discipline_id = self.discipline.currentData()
        lessons = (
            self.service.list_source_lessons(discipline_id) if discipline_id is not None else []
        )
        discipline = next(
            (d for d in self.service.list_disciplines() if d.id == discipline_id), None
        )
        self.description.setText(
            discipline.description
            if discipline
            else "Создайте дисциплину и занятие. Список студентов можно добавить позднее."
        )
        self.description.setToolTip(self.description.text())
        self.description.setVisible(bool(self.description.text().strip()))
        fill_table(
            self.lessons,
            [
                (
                    lesson.id,
                    TableValue(lesson_label(lesson), (lesson.starts_at, lesson.ends_at)),
                    lesson.title,
                    lesson.room,
                    STATES[lesson.state],
                    TableValue(
                        f"{counts.expected_students} чел. · "
                        + ("зафиксирован" if lesson.roster_fixed else "из дисциплины"),
                        counts.expected_students,
                    ),
                    _registration_counts(counts),
                )
                for lesson in lessons
                for counts in [self.service.lesson_counts(lesson.id)]
            ],
            [lesson.id for lesson in lessons],
        )
        self.filter_rows()
        for row in range(self.lessons.rowCount()):
            index = self.lessons.model().index(row, 0)
            if index.data(
                Qt.ItemDataRole.UserRole
            ) in selected_ids and not self.lessons.isRowHidden(row):
                self.lessons.selectionModel().select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        self.lessons.resizeRowsToContents()

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def filter_rows(self, *_) -> None:
        query = self.search.text()
        count = filter_table(self.lessons, query, columns=(0, 1, 2, 3, 4))
        selection = self.lessons.selectionModel()
        for index in selection.selectedRows():
            if self.lessons.isRowHidden(index.row()):
                selection.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Deselect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        self.matches.setText(f"Показано {count} из {self.lessons.rowCount()}")
        self.matches.setVisible(bool(query.strip()))

    def update_counts(self, lesson_id: int | None = None) -> None:
        """Update committed counts without resetting the operator's selection."""
        sorting = self.lessons.isSortingEnabled()
        self.lessons.setSortingEnabled(False)
        for row in range(self.lessons.rowCount()):
            key = self.lessons.item(row, 0)
            if key is None:
                continue
            current_id = key.data(Qt.ItemDataRole.UserRole)
            if lesson_id is not None and current_id != lesson_id:
                continue
            counts = self.service.lesson_counts(current_id)
            item = self.lessons.item(row, 6)
            if isinstance(item, TableCell):
                value = _registration_counts(counts)
                item.order = value.order
                item.setText(value.text)
        self.lessons.setSortingEnabled(sorting)
        self.lessons.resizeRowsToContents()

    def selected_lesson_id(self) -> int | None:
        selected = selected_key(self.lessons)
        return int(str(selected)) if selected is not None else None

    def _open(self) -> None:
        lesson_id = self.selected_lesson_id()
        if lesson_id is not None:
            self.open_lesson(lesson_id)
        else:
            private_message(
                self, "Выберите занятие", "Сначала создайте или выберите строку занятия."
            )

    def _logs(self) -> None:
        lesson_id = self.selected_lesson_id()
        if lesson_id is not None:
            self.view_log(lesson_id)

    def edit_discipline(self, edit: bool) -> None:
        discipline_id = self.discipline.currentData()
        existing = (
            next((d for d in self.service.list_disciplines() if d.id == discipline_id), None)
            if edit
            else None
        )
        if edit and existing is None:
            return
        dialog = DisciplineDialog(self, discipline=existing)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def save() -> None:
            values = dialog.values()
            if existing:
                values["discipline_id"] = existing.id
            saved = self.service.save_discipline(**values)
            self.changed()
            self.discipline.setCurrentIndex(self.discipline.findData(saved.id))

        guarded(self, save)

    def edit_lesson(self, edit: bool) -> None:
        discipline_id = self.discipline.currentData()
        if discipline_id is None:
            private_message(self, "Нужна дисциплина", "Сначала добавьте дисциплину.")
            return
        selected_id = self.selected_lesson_id()
        existing = (
            self.service.get_lesson(selected_id) if edit and selected_id is not None else None
        )
        if edit and existing is None:
            return
        dialog = LessonDialog(discipline_id, self.default_timezone, self, lesson=existing)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def save() -> None:
            saved = self.service.save_lesson(**dialog.values())
            self.changed()
            for index in range(self.lessons.rowCount()):
                item = self.lessons.item(index, 0)
                if (
                    item is not None
                    and item.data(Qt.ItemDataRole.UserRole) == saved.id
                    and not self.lessons.isRowHidden(index)
                ):
                    self.lessons.selectRow(index)
                    break
            overlaps = self.service.overlapping_lessons(saved)
            if overlaps:
                private_message(
                    self,
                    "Пересечение расписания",
                    "Занятие сохранено. Время пересекается с занятиями:\n"
                    + "\n".join(lesson_label(item) for item in overlaps)
                    + "\n\nРегистрация по-прежнему открывается только вручную.",
                )

        guarded(self, save)

    def discipline_roster(self) -> None:
        discipline_id = self.discipline.currentData()
        if discipline_id is None:
            return
        dialog = StudentSelectionDialog(
            self.service.list_students(),
            self.service.get_discipline_roster(discipline_id),
            self,
            title="Ожидаемый состав дисциплины",
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:

            def save() -> None:
                self.service.set_discipline_roster(discipline_id, dialog.selected_ids())
                self.changed()

            guarded(self, save)

    def lesson_rosters(self) -> None:
        lesson_ids = [
            int(index.data(Qt.ItemDataRole.UserRole))
            for index in self.lessons.selectionModel().selectedRows()
            if not self.lessons.isRowHidden(index.row())
        ]
        if not lesson_ids:
            private_message(
                self, "Выберите занятия", "Выберите одно или несколько занятий в таблице."
            )
            return
        selected = self.service.get_lesson_roster(lesson_ids[0])
        dialog = StudentSelectionDialog(
            self.service.list_students(),
            selected,
            self,
            title=f"Состав выбранных занятий ({len(lesson_ids)})",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        students = dialog.selected_ids()
        names = "\n".join(lesson_label(self.service.get_lesson(key)) for key in lesson_ids)
        if not private_message(
            self,
            "Назначить состав занятий?",
            f"Выбранный состав ({len(students)} чел.) заменит ожидаемый состав {len(lesson_ids)} занятий:\n{names}\n\nСохранённые предъявления карт останутся в журнале. Состав других занятий не изменится.",
            question=True,
        ):
            return

        def save() -> None:
            for lesson_id in lesson_ids:
                self.service.set_lesson_roster(lesson_id, students)
            self.changed()

        guarded(self, save)

    def delete_selected(self):
        delete_lesson_dialog(self, self.service, self.selected_lesson_id(), self.changed)

    def delete_selected_with_key(self):
        if len(self.lessons.selectionModel().selectedRows()) == 1:
            self.delete_selected()
