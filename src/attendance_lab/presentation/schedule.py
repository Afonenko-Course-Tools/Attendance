"""Discipline schedules and explicit expected rosters."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.service import AttendanceService
from attendance_lab.presentation.common import (
    STATES,
    actions,
    fill_table,
    guarded,
    lesson_label,
    plain_label,
    private_message,
    selected_key,
    table,
)
from attendance_lab.presentation.dialogs import (
    DisciplineDialog,
    LessonDialog,
    StudentSelectionDialog,
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
        layout.setContentsMargins(18, 18, 18, 18)
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
            )
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
        layout.addWidget(self.lessons, 1)
        layout.addLayout(
            actions(
                ("Добавить занятие", lambda: self.edit_lesson(False)),
                ("Изменить занятие", lambda: self.edit_lesson(True)),
                ("Состав выбранных занятий…", self.lesson_rosters),
            )
        )
        layout.addLayout(
            actions(
                ("Открыть регистрацию на выбранное занятие", self._open),
                ("Посмотреть приватный журнал", self._logs),
            )
        )
        hint = plain_label(
            "Выбор дисциплины или строки меняет только просмотр. Регистрация переключается отдельной кнопкой. Для назначения состава нескольким занятиям используйте Ctrl / Shift.",
            wrap=True,
        )
        hint.setObjectName("Muted")
        layout.addWidget(hint)
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
        discipline_id = self.discipline.currentData()
        lessons = self.service.list_lessons(discipline_id) if discipline_id is not None else []
        discipline = next(
            (d for d in self.service.list_disciplines() if d.id == discipline_id), None
        )
        self.description.setText(
            discipline.description
            if discipline
            else "Создайте дисциплину и занятие. Список студентов можно добавить позднее."
        )
        fill_table(
            self.lessons,
            [
                (
                    lesson.id,
                    lesson_label(lesson),
                    lesson.title,
                    lesson.room,
                    STATES[lesson.state],
                    f"{counts.expected_students} чел. · "
                    + ("зафиксирован" if lesson.roster_fixed else "из дисциплины"),
                    f"Карт: {counts.unique_cards} · Студентов: {counts.identified_students}\n"
                    f"Без сопоставления: {counts.unidentified_cards}",
                )
                for lesson in lessons
                for counts in [self.service.lesson_counts(lesson.id)]
            ],
            [lesson.id for lesson in lessons],
        )
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
                if item is not None and item.text() == str(saved.id):
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
