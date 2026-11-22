"""Private student directory and historical card associations."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.files import CsvPort
from attendance_lab.application.service import AttendanceService
from attendance_lab.presentation.attendance_models import StudentSearch, normalized
from attendance_lab.presentation.common import (
    TableValue,
    actions,
    fill_table,
    guarded,
    plain_label,
    private_message,
    search_field,
    table,
)
from attendance_lab.presentation.dialogs import (
    BindingDialog,
    ExportOptionsDialog,
    ImportStudentsDialog,
    StudentDialog,
)
from attendance_lab.presentation.import_students import ImportPreviewDialog
from attendance_lab.presentation.table_columns import TableColumns


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
        self.binding_changed: Callable[[str], None] = lambda _uid: self.changed()
        self._students_by_id = {}
        self._contexts_by_id = {}
        self._cards = []
        self._search_index = StudentSearch(())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        toolbar = actions(
            ("Добавить студента", lambda: self.edit_student(False)),
            ("Изменить студента", lambda: self.edit_student(True)),
            ("Экспорт студентов CSV…", self.export_students),
        )
        import_button = QPushButton("Импорт CSV…")
        import_menu = QMenu(import_button)
        import_menu.addAction("Учебный список с группами…", self.import_academic_students)
        import_menu.addAction("Список со стабильными ID…", self.import_students)
        import_button.setMenu(import_menu)
        toolbar.insertWidget(2, import_button)
        self.unbind_button = QPushButton("Снять привязку карты · Ctrl+U")
        self.unbind_button.setEnabled(False)
        self.unbind_button.clicked.connect(self.unbind_selected_student)
        toolbar.insertWidget(0, self.unbind_button)
        layout.addLayout(toolbar)
        search_bar = QHBoxLayout()
        self.search = search_field("ФИО, группа, специальность или № зачётки · F3")
        search_bar.addWidget(self.search, 1)
        self.course = QComboBox()
        self.course.addItem("Все курсы", None)
        for number in range(1, 7):
            self.course.addItem(f"{number} курс", number)
        search_bar.addWidget(self.course)
        layout.addLayout(search_bar)
        self.students = table(
            ("ФИО", "Курс", "Группа", "Специальность", "№ зачётки", "Карт"), sortable=True
        )
        self.student_columns = TableColumns(
            self.students,
            "students",
            keys=("name", "year", "group", "specialty", "record", "cards"),
        )
        self.student_columns.restore_visibility()
        layout.addWidget(self.students, 1)
        note = plain_label(
            "Выберите студента и снимите ошибочную привязку. Чтобы исправить предъявленную карту, нажмите F4 на странице занятия.",
            wrap=True,
        )
        note.setObjectName("Muted")
        layout.addWidget(note)
        self.search.textChanged.connect(self._filter_students)
        self.course.currentIndexChanged.connect(self._filter_students)
        self.students.itemSelectionChanged.connect(self._student_selection_changed)
        self.next_student = QShortcut(QKeySequence("Down"), self.search)
        self.next_student.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.next_student.activated.connect(self.focus_students)
        self.unbind_shortcut = QShortcut(QKeySequence("Ctrl+U"), self)
        self.unbind_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.unbind_shortcut.activated.connect(self.unbind_selected_student)

    def refresh(self) -> None:
        self._students_by_id = {s.id: s for s in self.service.list_students()}
        contexts = self.service.redesign.list_student_contexts()
        self._contexts_by_id = {s.student_id: s for s in contexts}
        self._search_index = StudentSearch(contexts)
        self._cards = self.service.list_cards()
        self._filter_students()

    def _filter_students(self, *_args) -> None:
        search = normalized(self.search.text()).strip()
        found = {s.student_id for s in self._search_index.find(search)}
        course = self.course.currentData()
        card_counts = {}
        for card in self._cards:
            if card.student_id:
                card_counts[card.student_id] = card_counts.get(card.student_id, 0) + 1
        students = [
            s
            for s in self._students_by_id.values()
            if (
                not search
                or s.id in found
                or all(part in normalized(f"{s.full_name} {s.group}") for part in search.split())
            )
            and (
                course is None
                or (
                    self._contexts_by_id.get(s.id) is not None
                    and self._contexts_by_id[s.id].course == course
                )
            )
        ]
        selected = self._selected_student_id()
        fill_table(
            self.students,
            [
                (
                    s.full_name,
                    TableValue(
                        str(study_course)
                        if (study_course := getattr(self._contexts_by_id.get(s.id), "course", None))
                        else "—",
                        study_course or 0,
                    ),
                    self._contexts_by_id[s.id].group_number or s.group or "—"
                    if s.id in self._contexts_by_id
                    else s.group or "—",
                    self._contexts_by_id[s.id].specialty or "—"
                    if s.id in self._contexts_by_id
                    else "—",
                    self._contexts_by_id[s.id].record_book_number or "—"
                    if s.id in self._contexts_by_id
                    else "—",
                    card_counts.get(s.id, 0),
                )
                for s in students
            ],
            [s.id for s in students],
        )
        if selected not in {s.id for s in students}:
            self.students.clearSelection()
            self.students.setCurrentCell(-1, -1)
        self._student_selection_changed()

    def _student_selection_changed(self) -> None:
        student_id = self._selected_student_id()
        self.unbind_button.setEnabled(
            student_id is not None and any(c.student_id == student_id for c in self._cards)
        )

    def _selected_student_id(self) -> str | None:
        rows = self.students.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        value = rows[0].data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else None

    def focus_students(self) -> None:
        if self.students.rowCount():
            self.students.selectRow(0)
            self.students.setFocus()

    def unbind_selected_student(self) -> None:
        student_id = self._selected_student_id()
        if student_id is None:
            return
        student = self._students_by_id.get(student_id)
        if student is None:
            return
        uids = {card.uid for card in self._cards if card.student_id == student.id}
        if not uids:
            return
        count = len(uids)
        detail = (
            f"У студента записано {count} карт. Будут сняты ВСЕ эти привязки. "
            "Чтобы исправить только одну, приложите её и нажмите F4 на странице занятия.\n\n"
            if count > 1
            else ""
        )
        if not private_message(
            self,
            "Снять привязку карты?",
            f"Студент: {student.full_name} · группа {student.group or '—'}\n\n"
            + detail
            + ("Посещения по этим картам" if count > 1 else "Посещения по этой карте")
            + " на всех занятиях больше не будут относиться к студенту. "
            "Сами предъявления и их время сохранятся. Снять привязку?",
            question=True,
        ):
            return

        def save() -> bool:
            self.service.unbind_student_cards(student.id, expected_uids=uids)
            for uid in sorted(uids):
                self.binding_changed(uid)
            return True

        if guarded(self, save) is None:
            self.refresh()

    def edit_student(self, edit: bool) -> None:
        student_id = self._selected_student_id()
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

    def import_academic_students(self) -> None:
        ImportPreviewDialog(self.service, self.changed, self).exec()

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

    def bind_uid(self, uid: str, correction: bool = False) -> bool:
        card = next((card for card in self.service.list_cards() if card.uid == uid), None)
        if card is None:
            private_message(
                self,
                "Карта не найдена",
                "Сначала сохраните предъявление карты на открытом занятии.",
            )
            return False
        correction = card.student_id is not None
        students = self.service.list_students()
        if not students and not correction:
            private_message(
                self,
                "Нет студентов",
                "Добавьте студента или импортируйте CSV. Предъявления карт уже сохранены.",
            )
            return False
        dialog = BindingDialog(
            card,
            students,
            self,
            correction=correction,
            contexts=self.service.redesign.list_student_contexts(),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:

            def save() -> bool:
                student_id = dialog.student_id()
                if student_id is None:
                    assert card.student_id is not None
                    self.service.unbind_card(uid, expected_student_id=card.student_id)
                else:
                    self.service.bind_card(
                        uid,
                        student_id,
                        correction=correction,
                        expected_student_id=card.student_id if correction else None,
                    )
                self.binding_changed(uid)
                return True

            return guarded(self, save) is True
        return False
