"""Private student directory and historical card associations."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.files import CsvPort
from attendance_lab.application.service import AttendanceService
from attendance_lab.presentation.common import (
    actions,
    fill_table,
    guarded,
    plain_label,
    private_message,
    selected_key,
    table,
)
from attendance_lab.presentation.dialogs import (
    BindingDialog,
    ExportOptionsDialog,
    ImportStudentsDialog,
    StudentDialog,
)


class StudentsPane(QWidget):
    def __init__(
        self,
        service: AttendanceService,
        files: CsvPort,
        changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.files = files
        self.changed = changed
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск студента: ФИО, группа или ID")
        layout.addWidget(self.search)
        splitter = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        self.students = table(("ID студента", "ФИО", "Группа"))
        top_layout.addWidget(self.students)
        top_layout.addLayout(
            actions(
                ("Добавить студента", lambda: self.edit_student(False)),
                ("Изменить студента", lambda: self.edit_student(True)),
                ("Импорт CSV…", self.import_students),
                ("Экспорт студентов CSV…", self.export_students),
            )
        )
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(plain_label("Приватный список карт — UID"))
        self.unknown_only = QCheckBox("Только несопоставленные")
        bar.addStretch()
        bar.addWidget(self.unknown_only)
        bottom_layout.addLayout(bar)
        self.cards = table(("UID", "Студент", "ID студента", "Занятий в истории"))
        bottom_layout.addWidget(self.cards)
        bottom_layout.addLayout(
            actions(
                ("Сопоставить карту…", lambda: self._selected_bind(False)),
                ("Исправить ошибочную привязку…", lambda: self._selected_bind(True)),
            )
        )
        note = plain_label(
            "Позднее сопоставление раскрывает уже сохранённые отметки. Исправление меняет связь всей истории карты; передачу карты другому студенту эта версия не учитывает.",
            wrap=True,
        )
        note.setObjectName("Muted")
        bottom_layout.addWidget(note)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setSizes([300, 300])
        layout.addWidget(splitter, 1)
        self.search.textChanged.connect(lambda *_: self.refresh())
        self.unknown_only.toggled.connect(lambda *_: self.refresh())

    def refresh(self) -> None:
        search = self.search.text().casefold().strip()
        students = [
            s
            for s in self.service.list_students()
            if not search or search in f"{s.id} {s.full_name} {s.group}".casefold()
        ]
        fill_table(
            self.students,
            [(s.id, s.full_name, s.group) for s in students],
            [s.id for s in students],
        )
        cards = [
            card
            for card in self.service.list_cards()
            if not self.unknown_only.isChecked() or card.student_id is None
        ]
        fill_table(
            self.cards,
            [
                (
                    card.uid,
                    card.full_name or "Не сопоставлена",
                    card.student_id or "—",
                    card.lesson_count,
                )
                for card in cards
            ],
            [card.uid for card in cards],
        )

    def edit_student(self, edit: bool) -> None:
        student_id = selected_key(self.students)
        student = (
            next((s for s in self.service.list_students() if s.id == student_id), None)
            if edit
            else None
        )
        if edit and student is None:
            return
        dialog = StudentDialog(self, student=student)
        if dialog.exec() == QDialog.DialogCode.Accepted:

            def save() -> None:
                if edit:
                    self.service.save_student(**dialog.values())
                else:
                    self.service.create_student(**dialog.values())
                self.changed()

            guarded(self, save)

    def import_students(self) -> None:
        dialog = ImportStudentsDialog(self.service, self.files, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.changed()

    def export_students(self) -> None:
        options = ExportOptionsDialog(self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Экспорт списка студентов со стабильными ID", "students.csv", "CSV (*.csv)"
        )
        if filename:
            rows = [(s.id, s.full_name, s.group) for s in self.service.list_students()]
            guarded(
                self,
                lambda: self.files.write_csv(
                    Path(filename),
                    ["id", "full_name", "group"],
                    rows,
                    delimiter=options.delimiter,
                    bom=options.bom,
                ),
            )

    def _selected_bind(self, correction: bool) -> None:
        uid = selected_key(self.cards)
        if uid is not None:
            self.bind_uid(str(uid), correction)

    def bind_uid(self, uid: str, correction: bool = False) -> None:
        card = next((card for card in self.service.list_cards() if card.uid == uid), None)
        if card is None:
            private_message(
                self,
                "Карта не найдена",
                "Сначала сохраните предъявление карты на открытом занятии.",
            )
            return
        students = self.service.list_students()
        if not students:
            private_message(
                self,
                "Нет студентов",
                "Добавьте студента или импортируйте CSV. Предъявления карт уже сохранены.",
            )
            return
        if card.student_id and not correction:
            private_message(
                self,
                "Карта уже сопоставлена",
                f"Текущее сопоставление: {card.full_name} ({card.student_id}).\nДля изменения используйте отдельное действие «Исправить ошибочную привязку».",
            )
            return
        if correction and not card.student_id:
            private_message(
                self,
                "Карта ещё не сопоставлена",
                "Для этой карты используйте обычное сопоставление.",
            )
            return
        dialog = BindingDialog(card, students, self, correction=correction)
        if dialog.exec() == QDialog.DialogCode.Accepted:

            def save() -> None:
                self.service.bind_card(uid, dialog.student_id(), correction=correction)
                self.changed()

            guarded(self, save)
