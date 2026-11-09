"""Relational academic preparation, preview imports and fixed-slot scheduling."""

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.presentation.common import guarded, plain_label
from attendance_lab.presentation.import_students import ImportPreviewDialog


class FormDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(650, 450)
        self.outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.form = QFormLayout(content)
        scroll.setWidget(content)
        self.outer.addWidget(scroll)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.save_shortcut.activated.connect(
            lambda: self.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        )

    def finish(self):
        self.outer.addWidget(self.buttons)


def combo(items):
    control = QComboBox()
    for text, key in items:
        control.addItem(text, key)
    return control


def spin(low, high, value):
    control = QSpinBox()
    control.setRange(low, high)
    control.setValue(value)
    return control


def choices(items, selected=()):
    control = QListWidget()
    for text, key in items:
        item = QListWidgetItem(text, control)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setCheckState(Qt.CheckState.Checked if key in selected else Qt.CheckState.Unchecked)
    return control


def checked(control):
    return {
        control.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(control.count())
        if control.item(i).checkState() == Qt.CheckState.Checked
    }


class PreparationPane(QWidget):
    def __init__(self, service, changed, schedule, students, parent=None):
        super().__init__(parent)
        self.service, self.api, self.changed = service, service.redesign, changed
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "Общий учебный список: импорт не добавляет группы в курс автоматически.", wrap=True
            )
        )
        toolbar = QHBoxLayout()
        button = QPushButton("Импорт студентов CSV…")
        button.clicked.connect(self.import_csv)
        toolbar.addWidget(button)
        catalogs = QToolButton()
        catalogs.setText("Учебные справочники…")
        catalogs.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(catalogs)
        for label, callback in [
            ("Учебный год…", self.year_dialog),
            ("Группа…", self.group_dialog),
            ("Студент…", self.student_dialog),
            ("Специальности и профили…", self.catalog_dialog),
        ]:
            menu.addAction(label, callback)
        catalogs.setMenu(menu)
        toolbar.addWidget(catalogs)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.summary = plain_label(wrap=True)
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        self.schedule_window = QDialog(self)
        self.schedule_window.setWindowTitle("Исходные занятия — расписание и история")
        self.schedule_window.resize(1000, 700)
        self.schedule_window.setMinimumSize(900, 600)
        source_layout = QVBoxLayout(self.schedule_window)
        source_layout.addWidget(schedule)
        source = QWidget()
        source_box = QVBoxLayout(source)
        source_box.addWidget(
            plain_label(
                "Исходное расписание доступно в отдельном окне. Здесь можно удалить фиктивное занятие или восстановить ранее удалённое.",
                wrap=True,
            )
        )
        self.source_schedule_button = QPushButton("Исходные занятия…")
        self.source_schedule_button.clicked.connect(self.schedule_window.show)
        source_box.addWidget(self.source_schedule_button)
        source_box.addStretch()
        self.tabs.addTab(source, "Исходное расписание")
        self.tabs.addTab(students, "Учебный список")
        layout.addWidget(self.tabs)

    def refresh(self):
        self.summary.setText(
            f"Учебных лет: {len(self.api.list_years())} · Групп: {len(self.api.list_groups())} · Составов: {len(self.api.list_rosters())} · Блоков: {len(self.api.list_blocks())}"
        )

    def run_form(self, dialog, save):
        dialog.finish()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            guarded(self, lambda: (save(), self.changed()))

    def import_csv(self):
        ImportPreviewDialog(self.service, self.changed, self).exec()

    def year_dialog(self):
        dialog = FormDialog("Учебный год", self)
        year = spin(2000, 2200, date.today().year)
        dialog.form.addRow("Начальный год", year)
        self.run_form(dialog, lambda: self.api.save_year(year.value()))

    def group_dialog(self):
        dialog = FormDialog("Академическая группа", self)
        year = combo([(f"{y.start_year}/{y.start_year + 1}", y.id) for y in self.api.list_years()])
        specialty = combo([(s.name, s.id) for s in self.api.list_specialties()])
        course, number, variant = spin(1, 6, 1), spin(1, 999, 1), QLineEdit()
        existing = combo(
            [("Новая группа", None)]
            + [
                (f"{g.specialty_name} · курс {g.course} · гр. {g.number} · {g.variant_key}", g)
                for g in self.api.list_groups()
            ]
        )

        def load_group():
            value = existing.currentData()
            if value:
                year.setCurrentIndex(year.findData(value.academic_year_id))
                specialty.setCurrentIndex(specialty.findData(value.specialty_id))
                course.setValue(value.course)
                number.setValue(value.number)
                variant.setText(value.variant_key)

        existing.currentIndexChanged.connect(load_group)
        dialog.form.addRow("Редактировать", existing)
        for label, control in [
            ("Учебный год", year),
            ("Специальность", specialty),
            ("Курс", course),
            ("Номер", number),
            ("Вариант", variant),
        ]:
            dialog.form.addRow(label, control)
        self.run_form(
            dialog,
            lambda: self.api.save_group(
                year.currentData(),
                specialty.currentData(),
                course.value(),
                number.value(),
                variant.text(),
                group_id=existing.currentData().id if existing.currentData() else None,
            ),
        )

    def student_dialog(self):
        dialog = FormDialog("Контекст студента", self)
        student = combo(
            [
                (f"{s.full_name} · {s.record_book_number}", s)
                for s in self.api.list_student_contexts()
            ]
        )
        name, book = QLineEdit(), QLineEdit()
        groups = combo(
            [("Без группы", None)]
            + [
                (f"{g.specialty_name} · {g.course} курс · {g.number} · {g.variant_key}", g)
                for g in self.api.list_groups()
            ]
        )
        profiles = combo(
            [("Без профиля", None)] + [(p.name, p.id) for p in self.api.list_profiles()]
        )

        def load():
            value = student.currentData()
            if value:
                name.setText(value.full_name)
                book.setText(value.record_book_number)

        student.currentIndexChanged.connect(load)
        load()
        for label, control in [
            ("Студент", student),
            ("ФИО", name),
            ("Зачётная книжка", book),
            ("Группа", groups),
            ("Профиль", profiles),
        ]:
            dialog.form.addRow(label, control)

        def save():
            s, g = student.currentData(), groups.currentData()
            self.api.save_student_context(
                s.student_id,
                name.text(),
                book.text(),
                g.academic_year_id if g else None,
                g.id if g else None,
                profiles.currentData(),
            )

        self.run_form(dialog, save)

    def catalog_dialog(self):
        dialog = FormDialog("Каталоги и псевдонимы", self)
        kind = combo([("Специальности", "specialty"), ("Профили", "profile")])
        entry, name, alias = (
            QComboBox(),
            QLineEdit(),
            QCheckBox("Добавить псевдоним вместо переименования"),
        )

        def load():
            entry.clear()
            for item in (
                self.api.list_specialties()
                if kind.currentData() == "specialty"
                else self.api.list_profiles()
            ):
                entry.addItem(item.name, item.id)

        kind.currentIndexChanged.connect(load)
        load()
        for label, control in [
            ("Каталог", kind),
            ("Запись", entry),
            ("Название", name),
            ("", alias),
        ]:
            dialog.form.addRow(label, control)
        self.run_form(
            dialog,
            lambda: getattr(
                self.api,
                ("add_" if alias.isChecked() else "rename_")
                + kind.currentData()
                + ("_alias" if alias.isChecked() else ""),
            )(entry.currentData(), name.text()),
        )

    def enrollment_dialog(self):
        dialog = FormDialog("Группы дисциплины", self)
        discipline = combo([(d.name, d.id) for d in self.service.list_disciplines()])
        year = combo([(str(y.start_year), y.id) for y in self.api.list_years()])
        groups = QListWidget()

        def load_groups():
            groups.clear()
            selected = (
                self.api.get_discipline_groups(discipline.currentData(), year.currentData())
                if discipline.currentData() is not None and year.currentData() is not None
                else set()
            )
            for group in self.api.list_groups():
                if group.academic_year_id != year.currentData():
                    continue
                item = QListWidgetItem(
                    f"{group.specialty_name} · курс {group.course} · группа {group.number}", groups
                )
                item.setData(Qt.ItemDataRole.UserRole, group.id)
                item.setCheckState(
                    Qt.CheckState.Checked if group.id in selected else Qt.CheckState.Unchecked
                )

        discipline.currentIndexChanged.connect(load_groups)
        year.currentIndexChanged.connect(load_groups)
        load_groups()
        dialog.form.addRow("Дисциплина", discipline)
        dialog.form.addRow("Учебный год", year)
        dialog.form.addRow("Группы", groups)
        self.run_form(
            dialog,
            lambda: self.api.set_discipline_groups(
                discipline.currentData(), year.currentData(), checked(groups)
            ),
        )

    def roster_dialog(self):
        dialog = FormDialog("Состав семестра", self)
        discipline = combo([(d.name, d.id) for d in self.service.list_disciplines()])
        year = combo([(str(y.start_year), y.id) for y in self.api.list_years()])
        semester, name = spin(1, 2, 1), QLineEdit()
        kind = combo(
            [
                ("Лекция", "lecture"),
                ("Лабораторная", "lab"),
                ("Семинар", "seminar"),
                ("Другое", "other"),
            ]
        )
        mode = combo([("Целые группы", "groups"), ("Выбранные студенты одной группы", "students")])
        duration = spin(1, 2, 1)
        groups = choices(
            [
                (f"{g.specialty_name} · {g.course} курс · {g.number}", g.id)
                for g in self.api.list_groups()
            ]
        )
        students = choices(
            [
                (f"{s.full_name} · {s.record_book_number} · {s.group_number}", s.student_id)
                for s in self.api.list_student_contexts()
            ]
        )
        existing = combo(
            [("Новый состав", None)]
            + [(f"{r.name} · семестр {r.semester}", r) for r in self.api.list_rosters()]
        )

        def load_roster():
            value = existing.currentData()
            if value is None:
                return
            discipline.setCurrentIndex(discipline.findData(value.discipline_id))
            year.setCurrentIndex(year.findData(value.academic_year_id))
            semester.setValue(value.semester)
            name.setText(value.name)
            kind.setCurrentIndex(kind.findData(value.kind))
            mode.setCurrentIndex(mode.findData(value.mode))
            duration.setValue(value.duration)
            for control, ids in ((groups, value.group_ids), (students, value.student_ids)):
                for i in range(control.count()):
                    item = control.item(i)
                    item.setCheckState(
                        Qt.CheckState.Checked
                        if item.data(Qt.ItemDataRole.UserRole) in ids
                        else Qt.CheckState.Unchecked
                    )

        existing.currentIndexChanged.connect(load_roster)
        for label, control in [
            ("Редактировать", existing),
            ("Дисциплина", discipline),
            ("Год", year),
            ("Семестр", semester),
            ("Название / подгруппа", name),
            ("Вид", kind),
            ("Длительность, пар", duration),
            ("Состав", mode),
            ("Группы", groups),
            ("Студенты", students),
        ]:
            dialog.form.addRow(label, control)
        self.run_form(
            dialog,
            lambda: self.api.save_roster(
                discipline.currentData(),
                year.currentData(),
                semester.value(),
                name.text(),
                kind.currentData(),
                2 if kind.currentData() == "lab" else duration.value(),
                mode.currentData(),
                checked(groups),
                checked(students),
                roster_id=existing.currentData().id if existing.currentData() else None,
            ),
        )

    def copy_dialog(self):
        dialog = FormDialog("Копировать состав в семестр", self)
        roster = combo([(r.name, r.id) for r in self.api.list_rosters()])
        semester = spin(1, 2, 2)
        dialog.form.addRow("Состав", roster)
        dialog.form.addRow("Семестр назначения", semester)
        self.run_form(dialog, lambda: self.api.copy_roster(roster.currentData(), semester.value()))

    def block_dialog(self):
        dialog = FormDialog("Блок занятий — фиксированные пары", self)
        roster = combo(
            [
                (f"{r.name} · семестр {r.semester} · {r.duration} пар.", r.id)
                for r in self.api.list_rosters()
            ]
        )
        on_date = QDateEdit(QDate.currentDate())
        on_date.setCalendarPopup(True)
        slot, title, room = QComboBox(), QLineEdit(), QLineEdit()
        durations = {r.id: r.duration for r in self.api.list_rosters()}
        times = (
            "09:00–10:20",
            "10:30–11:50",
            "12:00–13:20",
            "13:50–15:10",
            "15:20–16:40",
            "17:00–18:20",
            "18:30–19:50",
            "20:00–21:20",
        )

        def load_slots():
            selected = slot.currentData()
            slot.clear()
            duration = durations.get(roster.currentData(), 1)
            for index, time in enumerate(times[: 9 - duration], 1):
                slot.addItem(
                    f"{index}. {time}" + (f" + пара {index + 1}" if duration == 2 else ""), index
                )
            slot.setCurrentIndex(max(0, slot.findData(selected)))

        roster.currentIndexChanged.connect(load_slots)
        load_slots()
        for label, control in [
            ("Состав / длительность", roster),
            ("Дата", on_date),
            ("Начальная пара (1–8)", slot),
            ("Тема", title),
            ("Аудитория", room),
        ]:
            dialog.form.addRow(label, control)
        self.run_form(
            dialog,
            lambda: self.api.schedule_block(
                roster.currentData(),
                on_date.date().toPython(),
                slot.currentData(),
                title=title.text(),
                room=room.text(),
            ),
        )
