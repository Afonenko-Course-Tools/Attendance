"""Relational academic preparation, preview imports and fixed-slot scheduling."""

import csv
import hashlib
import io
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.presentation.common import actions, fill_table, guarded, plain_label, table


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


class ImportPreviewDialog(FormDialog):
    def __init__(self, service, changed, parent=None):
        super().__init__("Импорт студентов — предварительная проверка", parent)
        self.api, self.changed, self.plan = service.redesign, changed, None
        self.specialty_aliases, self.profile_aliases, self.group_choices = {}, {}, {}
        self.path = QLineEdit()
        self.year = spin(2000, 2200, date.today().year)
        self.encoding = combo([("UTF-8 BOM", "utf-8-sig"), ("Windows-1251", "cp1251")])
        self.delimiter = combo(
            [("Авто", None), ("Запятая", ","), ("Точка с запятой", ";"), ("Табуляция", "\t")]
        )
        self.form.addRow("Файл", self.path)
        browse = QPushButton("Выбрать CSV…")
        browse.clicked.connect(self.browse)
        self.form.addRow(browse)
        self.form.addRow("Начало учебного года", self.year)
        self.form.addRow("Кодировка", self.encoding)
        self.form.addRow("Разделитель", self.delimiter)
        self.preview = table(
            ("№", "ФИО", "Специальность", "Профиль", "Курс", "Группа", "Зачётная книжка")
        )
        self.form.addRow(self.preview)
        self.note = QTextEdit()
        self.note.setReadOnly(True)
        self.form.addRow(self.note)
        review = QPushButton("Проверить и показать изменения")
        review.clicked.connect(lambda: guarded(self, self.review))
        self.form.addRow(review)
        resolve = QPushButton("Сопоставить названия и неоднозначные группы…")
        resolve.clicked.connect(self.resolve)
        self.form.addRow(resolve)
        self.finish()
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        for control in (self.path, self.year, self.encoding, self.delimiter):
            signal = (
                control.textChanged
                if isinstance(control, QLineEdit)
                else control.valueChanged
                if isinstance(control, QSpinBox)
                else control.currentIndexChanged
            )
            signal.connect(self.invalidate)

    def invalidate(self, *_):
        self.plan = None
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "CSV списка студентов", "", "CSV (*.csv)")
        if path:
            self.path.setText(path)

    def review(self):
        raw = Path(self.path.text()).read_bytes()
        text = raw.decode(self.encoding.currentData())
        if not text.strip():
            raise ValueError("Файл пуст")
        delimiter = self.delimiter.currentData()
        if delimiter is None:
            # The required single header row identifies the separator without
            # confusing quoted commas or multiline student fields with columns.
            try:
                delimiter = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,\t").delimiter
            except csv.Error as exc:
                raise ValueError(
                    "Не удалось определить разделитель CSV. Выберите его явно."
                ) from exc
        rows = list(
            csv.reader(
                io.StringIO(text, newline=""),
                delimiter=delimiter,
                strict=True,
            )
        )
        if not rows:
            raise ValueError("Файл пуст")
        self.plan = self.api.preview_import(
            rows[0],
            rows[1:],
            self.year.value(),
            file_hash=hashlib.sha256(raw).hexdigest(),
            specialty_aliases=self.specialty_aliases,
            profile_aliases=self.profile_aliases,
            group_choices=self.group_choices,
        )
        fill_table(
            self.preview,
            [
                tuple(
                    str(getattr(row, key))
                    for key in (
                        "ordinal",
                        "full_name",
                        "specialty",
                        "profile",
                        "course",
                        "group_number",
                        "record_book_number",
                    )
                )
                for row in self.plan.rows
            ],
            [row.source_row for row in self.plan.rows],
        )
        self.note.setPlainText("\n".join((*self.plan.errors, *self.plan.changes)))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not self.plan.errors)

    def resolve(self):
        if self.plan is None:
            return
        dialog = FormDialog("Явное сопоставление импорта", self)
        use_specialty = QCheckBox("Сопоставить название специальности")
        source_specialty = combo(
            [(name, name) for name in sorted({r.specialty for r in self.plan.rows})]
        )
        specialty = combo([(s.name, s.id) for s in self.api.list_specialties()])
        use_profile = QCheckBox("Сопоставить название профиля")
        source_profile = combo(
            [(name, name) for name in sorted({r.profile for r in self.plan.rows})]
        )
        profile = combo([(p.name, p) for p in self.api.list_profiles()])
        use_group = QCheckBox("Выбрать группу для строки файла")
        source_row = combo(
            [
                (f"Строка {r.source_row}: {r.full_name} · гр. {r.group_number}", r.source_row)
                for r in self.plan.rows
            ]
        )
        group = combo(
            [
                (
                    f"{g.specialty_name} · курс {g.course} · гр. {g.number} · {g.variant_key} · ID {g.id}",
                    g.id,
                )
                for g in self.api.list_groups()
            ]
        )
        for label, control in [
            ("", use_specialty),
            ("В файле", source_specialty),
            ("В каталоге", specialty),
            ("", use_profile),
            ("В файле", source_profile),
            ("В каталоге", profile),
            ("", use_group),
            ("Строка", source_row),
            ("Группа", group),
        ]:
            dialog.form.addRow(label, control)
        dialog.finish()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if use_specialty.isChecked() and specialty.currentData() is not None:
                self.specialty_aliases[source_specialty.currentData()] = specialty.currentData()
            if use_profile.isChecked() and profile.currentData() is not None:
                selected = profile.currentData()
                self.profile_aliases[(selected.specialty_id, source_profile.currentData())] = (
                    selected.id
                )
            if use_group.isChecked() and group.currentData() is not None:
                self.group_choices[source_row.currentData()] = group.currentData()
            guarded(self, self.review)

    def accept(self):
        if self.plan is not None and not self.plan.errors:

            def apply():
                self.api.apply_import(self.plan)
                self.changed()
                super(ImportPreviewDialog, self).accept()

            guarded(self, apply)


class PreparationPane(QWidget):
    def __init__(self, service, changed, schedule, students, parent=None):
        super().__init__(parent)
        self.service, self.api, self.changed = service, service.redesign, changed
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label("Подготовка учебного года, семестровых составов и расписания", wrap=True)
        )
        layout.addLayout(
            actions(
                ("Импорт 7 колонок…", self.import_csv),
                ("Учебный год…", self.year_dialog),
                ("Группа…", self.group_dialog),
                ("Редактор студента…", self.student_dialog),
                ("Каталоги…", self.catalog_dialog),
            )
        )
        layout.addLayout(
            actions(
                ("Группы дисциплины…", self.enrollment_dialog),
                ("Состав семестра…", self.roster_dialog),
                ("Копировать состав…", self.copy_dialog),
                ("Запланировать блок…", self.block_dialog),
            )
        )
        self.summary = plain_label(wrap=True)
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        for pane, label in ((schedule, "Дисциплины и занятия"), (students, "Студенты и карты")):
            self.tabs.addTab(pane, label)
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
