"""Reviewed student imports with visible errors and explicit catalog decisions."""

import csv
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
)

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.redesign import AcademicGroup, ImportPlan, name_key
from attendance_lab.presentation.common import fill_table, guarded, plain_label, table


def dialog_buttons(dialog, action):
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.button(QDialogButtonBox.StandardButton.Save).setText(action)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    shortcut = QShortcut(QKeySequence("Ctrl+Return"), dialog)
    shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    shortcut.activated.connect(lambda: buttons.button(QDialogButtonBox.StandardButton.Save).click())
    return buttons


@dataclass(frozen=True)
class GroupConflict:
    specialty: str
    profile: str
    course: int
    number: int
    source_rows: tuple[int, ...]
    candidates: tuple[AcademicGroup, ...]


class ImportGroupsDialog(QDialog):
    def __init__(self, conflicts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выберите вариант группы")
        self.resize(900, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "Для этих студентов в базе найдено несколько групп с одинаковым номером, "
                "курсом и специальностью. Выберите вариант для каждой строки таблицы. "
                "Выбор применяется ко всем указанным студентам, отдельно для каждой профилизации.",
                wrap=True,
            )
        )
        listing = table(
            ("Специальность / профиль", "Курс", "Группа", "Студентов", "Вариант в базе")
        )
        fill_table(
            listing,
            [
                (
                    c.specialty + (f" / {c.profile}" if c.profile else ""),
                    c.course,
                    c.number,
                    len(c.source_rows),
                    "",
                )
                for c in conflicts
            ],
            range(len(conflicts)),
        )
        self.choices = []
        for row, conflict in enumerate(conflicts):
            choice = QComboBox()
            choice.addItem("Выберите вариант…", None)
            for group in conflict.candidates:
                choice.addItem(group.variant_key or "Основная группа (без уточнения)", group.id)
            listing.setCellWidget(row, 4, choice)
            choice.setToolTip("Строки CSV: " + ", ".join(map(str, conflict.source_rows)))
            self.choices.append((conflict, choice))
            choice.currentIndexChanged.connect(self.update_ready)
        layout.addWidget(listing)
        self.buttons = dialog_buttons(self, "Применить выбор")
        layout.addWidget(self.buttons)
        self.update_ready()

    def update_ready(self, *_):
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(
            all(choice.currentData() is not None for _, choice in self.choices)
        )

    def selections(self):
        return {
            row: choice.currentData()
            for conflict, choice in self.choices
            if choice.currentData() is not None
            for row in conflict.source_rows
        }


class ImportNamesDialog(QDialog):
    def __init__(self, importer):
        super().__init__(importer)
        self.setWindowTitle("Названия справочников — необязательно")
        self.resize(900, 560)
        self.importer, self.api = importer, importer.api
        self.specialty_boxes = {}
        self.profile_boxes = {}
        self.profile_selections = dict(importer.profile_aliases)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "Совпадающие названия используются автоматически. Новые специальности и "
                "профилизации будут созданы при импорте. Выбирайте существующее название "
                "только если в CSV иначе названа та же специальность или профилизация.",
                wrap=True,
            )
        )
        tabs = QTabWidget()
        specialties = table(("Название в CSV", "Что сделать при импорте"))
        names = sorted({row.specialty for row in importer.plan.rows})
        fill_table(specialties, [(name, "") for name in names], names)
        for row, name in enumerate(names):
            existing = self.api.resolve_specialty_name(name)
            choice = QComboBox()
            if existing is None:
                choice.addItem("Создать новую специальность", None)
            for specialty in self.api.list_specialties():
                if existing is None or existing == specialty.id:
                    choice.addItem("Использовать: " + specialty.name, specialty.id)
            selected = importer.specialty_aliases.get(name, existing)
            choice.setCurrentIndex(max(0, choice.findData(selected)))
            choice.setEnabled(existing is None)
            specialties.setCellWidget(row, 1, choice)
            self.specialty_boxes[name] = choice
        tabs.addTab(specialties, "Специальности")
        self.profiles = table(
            ("Специальность в CSV", "Профилизация в CSV", "Что сделать при импорте")
        )
        tabs.addTab(self.profiles, "Профилизации")
        layout.addWidget(tabs)
        self.buttons = dialog_buttons(self, "Применить названия")
        layout.addWidget(self.buttons)
        self.refresh_profiles()
        for choice in self.specialty_boxes.values():
            choice.currentIndexChanged.connect(self.refresh_profiles)

    def refresh_profiles(self, *_):
        for (specialty_id, name), choice in self.profile_boxes.items():
            self.profile_selections[(specialty_id, name)] = choice.currentData()
        self.profile_boxes.clear()
        sources = sorted({(r.specialty, r.profile) for r in self.importer.plan.rows if r.profile})
        fill_table(self.profiles, [(s, p, "") for s, p in sources], sources)
        for row, (specialty, name) in enumerate(sources):
            specialty_id = self.specialty_boxes[specialty].currentData()
            existing = (
                self.api.resolve_profile_name(specialty_id, name)
                if specialty_id is not None
                else None
            )
            choice = QComboBox()
            if existing is None:
                choice.addItem("Создать новую профилизацию", None)
            if specialty_id is not None:
                for profile in self.api.list_profiles(specialty_id):
                    if existing is None or existing == profile.id:
                        choice.addItem("Использовать: " + profile.name, profile.id)
            selected = self.profile_selections.get((specialty_id, name), existing)
            choice.setCurrentIndex(max(0, choice.findData(selected)))
            choice.setEnabled(specialty_id is not None and existing is None)
            self.profiles.setCellWidget(row, 2, choice)
            self.profile_boxes[(specialty_id, name)] = choice

    def selections(self):
        specialties = {
            name: choice.currentData()
            for name, choice in self.specialty_boxes.items()
            if choice.currentData() is not None and self.api.resolve_specialty_name(name) is None
        }
        profiles = {
            (sid, name): choice.currentData()
            for (sid, name), choice in self.profile_boxes.items()
            if sid is not None
            and choice.currentData() is not None
            and self.api.resolve_profile_name(sid, name) is None
        }
        return specialties, profiles


class ImportPreviewDialog(QDialog):
    def __init__(self, service: AttendanceService, changed: Callable, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Импорт студентов — проверка перед загрузкой")
        self.resize(1000, 720)
        self.setMinimumSize(720, 520)
        self.api, self.changed = service.redesign, changed
        self.plan: ImportPlan | None = None
        self.specialty_aliases, self.profile_aliases, self.group_choices = {}, {}, {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "Новые курсы, группы, специальности и профилизации добавляются автоматически. "
                "Студенты, которых нет в файле, сохраняются в базе.",
                wrap=True,
            )
        )
        self.path = QLineEdit()
        self.path.setPlaceholderText("Выберите CSV со списком студентов")
        file_row = QHBoxLayout()
        file_row.addWidget(self.path, 1)
        browse = QPushButton("Выбрать CSV…")
        browse.clicked.connect(self.browse)
        file_row.addWidget(browse)
        layout.addLayout(file_row)
        settings = QHBoxLayout()
        self.year = QSpinBox()
        self.year.setRange(2000, 2200)
        self.year.setValue(date.today().year if date.today().month >= 9 else date.today().year - 1)
        self.encoding, self.delimiter = QComboBox(), QComboBox()
        for title, value in (("UTF-8", "utf-8-sig"), ("Windows-1251", "cp1251")):
            self.encoding.addItem(title, value)
        for title, value in (
            ("Авто", None),
            ("Запятая", ","),
            ("Точка с запятой", ";"),
            ("Табуляция", "\t"),
        ):
            self.delimiter.addItem(title, value)
        for title, control in (
            ("Начало учебного года", self.year),
            ("Кодировка", self.encoding),
            ("Разделитель", self.delimiter),
        ):
            settings.addWidget(plain_label(title))
            settings.addWidget(control)
        settings.addStretch()
        layout.addLayout(settings)
        actions = QHBoxLayout()
        self.review_button = QPushButton("Проверить файл")
        self.review_button.clicked.connect(self.review)
        self.resolve_button = QPushButton("Выбрать варианты групп…")
        self.resolve_button.clicked.connect(self.resolve)
        self.names_button = QPushButton("Названия справочников…")
        self.names_button.setToolTip("Необязательно: объединить разные написания одного названия")
        self.names_button.clicked.connect(self.resolve_names)
        for button in (self.review_button, self.resolve_button, self.names_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = plain_label(wrap=True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.preview = table(
            (
                "Строка CSV",
                "№",
                "ФИО",
                "Специальность",
                "Профилизация",
                "Курс",
                "Группа",
                "Зачётная книжка",
            )
        )
        self.errors_view = QListWidget()
        self.errors_view.setStyleSheet("QListWidget { color: #991b1b; background: #fff7f7; }")
        self.note = QTextEdit()
        self.note.setReadOnly(True)
        self.tabs.addTab(self.preview, "Студенты")
        self.tabs.addTab(self.errors_view, "Ошибки")
        self.tabs.addTab(self.note, "Изменения")
        layout.addWidget(self.tabs, 1)
        self.buttons = dialog_buttons(self, "Импортировать")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        layout.addWidget(self.buttons)
        for control in (self.path, self.year, self.encoding, self.delimiter):
            signal = (
                control.textChanged
                if isinstance(control, QLineEdit)
                else control.valueChanged
                if isinstance(control, QSpinBox)
                else control.currentIndexChanged
            )
            signal.connect(self.settings_changed)
        self.invalidate()

    def set_status(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet(
            (
                "background: #fee2e2; color: #991b1b; "
                if error
                else "background: #e6f2ec; color: #164c36; "
            )
            + "padding: 10px; border-radius: 6px; font-weight: 600;"
        )

    def invalidate(self, *_):
        self.plan = None
        button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        button.setEnabled(False)
        button.setText("Импортировать")
        button.setToolTip("Сначала выберите CSV и проверьте файл")
        self.resolve_button.setEnabled(False)
        self.names_button.setEnabled(False)
        self.preview.setRowCount(0)
        self.errors_view.clear()
        self.note.clear()
        self.tabs.setTabText(1, "Ошибки")
        self.set_status(
            "Выберите CSV — файл будет проверен автоматически."
            if not self.path.text().strip()
            else "Параметры изменены. Нажмите «Проверить файл»."
        )

    def settings_changed(self, *_):
        self.specialty_aliases.clear()
        self.profile_aliases.clear()
        self.group_choices.clear()
        self.invalidate()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "CSV списка студентов", "", "CSV (*.csv)")
        if path:
            self.path.setText(path)
            self.review()

    def show_errors(self, errors):
        self.errors_view.clear()
        self.errors_view.addItems(errors)
        self.tabs.setTabText(1, f"Ошибки ({len(errors)})")
        self.tabs.setCurrentWidget(self.errors_view)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setToolTip(
            "Импорт заблокирован: исправьте ошибки и повторите проверку"
        )

    def review(self):
        self.invalidate()
        try:
            raw = Path(self.path.text()).read_bytes()
            text = raw.decode(self.encoding.currentData())
            if not text.strip():
                raise ValueError("Файл пуст.")
            delimiter = self.delimiter.currentData()
            if delimiter is None:
                try:
                    delimiter = (
                        csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,\t").delimiter
                    )
                except csv.Error as exc:
                    raise ValueError(
                        "Не удалось определить разделитель. Выберите его явно."
                    ) from exc
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
            self.plan = self.api.preview_import(
                rows[0],
                rows[1:],
                self.year.value(),
                file_hash=hashlib.sha256(raw).hexdigest(),
                specialty_aliases=self.specialty_aliases,
                profile_aliases=self.profile_aliases,
                group_choices=self.group_choices,
            )
        except UnicodeError:
            error = "Ошибка кодировки. Выберите Windows-1251 или пересохраните CSV в UTF-8."
        except (OSError, ValueError, csv.Error) as exc:
            error = "Не удалось проверить CSV: " + str(exc)
        else:
            self.render_plan(sum(not any(value.strip() for value in row) for row in rows[1:]))
            return
        self.show_errors([error])
        self.set_status(error, error=True)

    def render_plan(self, skipped):
        assert self.plan is not None
        fill_table(
            self.preview,
            [
                (
                    r.source_row,
                    r.ordinal,
                    r.full_name,
                    r.specialty,
                    r.profile,
                    r.course,
                    r.group_number,
                    r.record_book_number,
                )
                for r in self.plan.rows
            ],
            [r.source_row for r in self.plan.rows],
        )
        changes = list(self.plan.changes)
        if skipped:
            changes.insert(0, f"Пустых строк пропущено: {skipped}.")
        self.note.setPlainText("\n".join(changes))
        self.names_button.setEnabled(bool(self.plan.rows))
        conflicts = self.group_conflicts()
        self.resolve_button.setEnabled(bool(conflicts))
        self.resolve_button.setText(
            f"Выбрать варианты групп ({len(conflicts)})…"
            if conflicts
            else "Выбрать варианты групп…"
        )
        button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        button.setEnabled(not self.plan.errors)
        button.setText(f"Импортировать {len(self.plan.rows)} студентов")
        if self.plan.errors:
            self.show_errors(self.plan.errors)
            hint = (
                "Выберите варианты групп или исправьте строки CSV и повторите проверку."
                if conflicts
                else "Исправьте указанные строки CSV и повторите проверку."
            )
            self.set_status(
                f"Импорт заблокирован: ошибок {len(self.plan.errors)}. {hint}", error=True
            )
        else:
            courses = ", ".join(map(str, sorted({r.course for r in self.plan.rows})))
            self.set_status(
                f"Готово к импорту: {len(self.plan.rows)} студентов · курсы: {courses} · учебный год {self.year.value()}/{str(self.year.value() + 1)[-2:]}."
            )
            button.setToolTip("Загрузить проверенный список · Ctrl+Enter")
            self.tabs.setCurrentWidget(self.preview)

    def group_conflicts(self):
        if self.plan is None:
            return []
        year_id = next(
            (y.id for y in self.api.list_years() if y.start_year == self.plan.academic_year_start),
            None,
        )
        groups = self.api.list_groups(year_id) if year_id is not None else []
        choices = dict(self.plan.group_choices)
        cohorts = {}
        for row in self.plan.rows:
            sid = self.specialty_aliases.get(
                row.specialty, self.api.resolve_specialty_name(row.specialty)
            )
            matches = tuple(
                g
                for g in groups
                if g.specialty_id == sid and g.course == row.course and g.number == row.group_number
            )
            if len(matches) > 1 and choices.get(row.source_row) not in {g.id for g in matches}:
                key = (sid, row.course, row.group_number, name_key(row.profile))
                if key not in cohorts:
                    cohorts[key] = (row, matches, [])
                cohorts[key][2].append(row.source_row)
        return [
            GroupConflict(
                matches[0].specialty_name,
                row.profile,
                row.course,
                row.group_number,
                tuple(source_rows),
                matches,
            )
            for row, matches, source_rows in cohorts.values()
        ]

    def resolve(self):
        conflicts = self.group_conflicts()
        if not conflicts:
            return
        dialog = ImportGroupsDialog(conflicts, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.group_choices.update(dialog.selections())
            self.review()

    def resolve_names(self):
        if self.plan is None:
            return
        dialog = ImportNamesDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.specialty_aliases, self.profile_aliases = dialog.selections()
            self.group_choices.clear()
            self.review()

    def accept(self):
        if self.plan is None or self.plan.errors:
            return
        try:
            self.api.apply_import(self.plan)
        except Exception as exc:
            self.invalidate()
            self.show_errors([str(exc)])
            self.set_status("Импорт не выполнен. " + str(exc), error=True)
            return
        guarded(self, self.changed)
        super().accept()
