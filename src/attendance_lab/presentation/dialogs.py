"""Private operator dialogs; all imported text is displayed as plain text."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, QTimeZone
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.domain.models import CardInfo, Discipline, Lesson, Student

if TYPE_CHECKING:
    from attendance_lab.application.files import CsvPort, ImportPreview
    from attendance_lab.application.service import AttendanceService


def _label(text: str = "", *, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    return label


def _error(parent: QWidget, message: str) -> None:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Проверьте данные")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(str(message))
    dialog.exec()


def _buttons(dialog: QDialog, label: str = "Сохранить") -> QDialogButtonBox:
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(label)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


class _ValidatedDialog(QDialog):
    def values(self) -> dict[str, Any]:
        raise NotImplementedError

    def accept(self) -> None:
        try:
            self.values()
        except (ValueError, ZoneInfoNotFoundError) as exc:
            _error(self, str(exc))
            return
        super().accept()


class DisciplineDialog(_ValidatedDialog):
    def __init__(self, parent: QWidget | None = None, discipline: Discipline | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изменить дисциплину" if discipline else "Новая дисциплина")
        self.resize(490, 260)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(discipline.name if discipline else "")
        self.description_edit = QPlainTextEdit(discipline.description if discipline else "")
        form.addRow("Название:", self.name_edit)
        form.addRow("Описание:", self.description_edit)
        layout.addLayout(form)
        layout.addWidget(_buttons(self))
        self.name_edit.setFocus()

    def values(self) -> dict[str, Any]:
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("Введите название дисциплины.")
        return {"name": name, "description": self.description_edit.toPlainText().strip()}


def _qt_datetime(value: datetime) -> QDateTime:
    # Editors represent a wall-clock value; keep it independent of the host's
    # timezone so its DST rules cannot normalize times from the chosen zone.
    return QDateTime(
        QDate(value.year, value.month, value.day),
        QTime(value.hour, value.minute, value.second),
        QTimeZone.utc(),
    )


def _wall_datetime(editor: QDateTimeEdit) -> datetime:
    value = editor.dateTime()
    day, clock = value.date(), value.time()
    return datetime(day.year(), day.month(), day.day(), clock.hour(), clock.minute())


def _valid_occurrences(wall: datetime, zone: ZoneInfo) -> list[datetime]:
    """Return real instants, rejecting skipped DST wall times and duplicates."""
    occurrences: list[datetime] = []
    for fold in (0, 1):
        aware = wall.replace(tzinfo=zone, fold=fold)
        recovered = aware.astimezone(UTC).astimezone(zone)
        if recovered.replace(tzinfo=None) != wall:
            continue
        if not any(aware.utcoffset() == item.utcoffset() for item in occurrences):
            occurrences.append(aware)
    return occurrences


class LessonDialog(_ValidatedDialog):
    def __init__(
        self,
        discipline_id: int,
        default_timezone: str,
        parent: QWidget | None = None,
        lesson: Lesson | None = None,
    ) -> None:
        super().__init__(parent)
        self.discipline_id = discipline_id
        self.lesson = lesson
        self.setWindowTitle("Изменить занятие" if lesson else "Новое занятие")
        self.resize(570, 330)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        timezone_name = lesson.timezone if lesson else default_timezone
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        start = (
            lesson.starts_at.astimezone(zone)
            if lesson
            else datetime.now(zone).replace(second=0, microsecond=0)
        )
        end = lesson.ends_at.astimezone(zone) if lesson else start + timedelta(minutes=90)
        self.starts_edit = QDateTimeEdit()
        self.ends_edit = QDateTimeEdit()
        for editor, value in ((self.starts_edit, start), (self.ends_edit, end)):
            editor.setTimeZone(QTimeZone.utc())
            editor.setDateTime(_qt_datetime(value))
        self.start_occurrence = QComboBox()
        self.end_occurrence = QComboBox()
        for name, editor, choice in (
            ("Начало:", self.starts_edit, self.start_occurrence),
            ("Окончание:", self.ends_edit, self.end_occurrence),
        ):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd.MM.yyyy HH:mm")
            row = QHBoxLayout()
            row.addWidget(editor, 1)
            row.addWidget(choice)
            form.addRow(name, row)
        self.timezone_edit = QLineEdit(timezone_name)
        self.timezone_edit.setPlaceholderText("Europe/Minsk")
        self.title_edit = QLineEdit(lesson.title if lesson else "")
        self.room_edit = QLineEdit(lesson.room if lesson else "")
        form.addRow("Часовой пояс IANA:", self.timezone_edit)
        form.addRow("Название / тема:", self.title_edit)
        form.addRow("Аудитория:", self.room_edit)
        layout.addLayout(form)
        self.time_hint = _label()
        layout.addWidget(self.time_hint)
        layout.addWidget(
            _label(
                "Регистрация открывается и завершается вручную. Изменение времени "
                "не переносит сохранённые отметки."
            )
        )
        layout.addWidget(_buttons(self))
        self.starts_edit.dateTimeChanged.connect(self._refresh_occurrences)
        self.ends_edit.dateTimeChanged.connect(self._refresh_occurrences)
        self.timezone_edit.textChanged.connect(self._refresh_occurrences)
        self._refresh_occurrences()
        self.start_occurrence.setCurrentIndex(max(0, self.start_occurrence.findData(start.fold)))
        self.end_occurrence.setCurrentIndex(max(0, self.end_occurrence.findData(end.fold)))

    def _refresh_occurrences(self, *_args: Any) -> None:
        try:
            zone = ZoneInfo(self.timezone_edit.text().strip())
        except ZoneInfoNotFoundError, ValueError:
            self.time_hint.setText("Укажите существующий часовой пояс, например Europe/Minsk.")
            return
        warnings: list[str] = []
        for name, editor, choice in (
            ("Начало", self.starts_edit, self.start_occurrence),
            ("Окончание", self.ends_edit, self.end_occurrence),
        ):
            selected = choice.currentData()
            options = _valid_occurrences(_wall_datetime(editor), zone)
            choice.clear()
            for value in options:
                choice.addItem(f"UTC{value.strftime('%z')}", value.fold)
            index = choice.findData(selected)
            choice.setCurrentIndex(max(0, index))
            choice.setVisible(len(options) > 1)
            if not options:
                warnings.append(f"{name}: время пропущено при переводе часов.")
            elif len(options) > 1:
                warnings.append(f"{name}: выберите смещение UTC для повторяющегося времени.")
        self.time_hint.setText("\n".join(warnings))

    def values(self) -> dict[str, Any]:
        name = self.timezone_edit.text().strip()
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Неизвестный часовой пояс. Пример: Europe/Minsk или UTC.") from exc
        times: list[datetime] = []
        for editor, choice in (
            (self.starts_edit, self.start_occurrence),
            (self.ends_edit, self.end_occurrence),
        ):
            options = _valid_occurrences(_wall_datetime(editor), zone)
            if not options:
                raise ValueError("Выбранное время не существует из-за перевода часов.")
            times.append(
                next((item for item in options if item.fold == choice.currentData()), options[0])
            )
        if times[1].astimezone(UTC) <= times[0].astimezone(UTC):
            raise ValueError("Окончание занятия должно быть позже начала.")
        values: dict[str, Any] = {
            "discipline_id": self.discipline_id,
            "starts_at": times[0],
            "ends_at": times[1],
            "timezone": name,
            "title": self.title_edit.text().strip(),
            "room": self.room_edit.text().strip(),
        }
        if self.lesson is not None:
            values["lesson_id"] = self.lesson.id
        return values


class StudentDialog(_ValidatedDialog):
    def __init__(self, parent: QWidget | None = None, student: Student | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изменить студента" if student else "Добавить студента")
        self.resize(530, 210)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.id_edit = QLineEdit(student.id if student else "")
        self.id_edit.setReadOnly(student is not None)
        self.id_edit.setPlaceholderText("Пусто — создать ID автоматически")
        self.name_edit = QLineEdit(student.full_name if student else "")
        self.group_edit = QLineEdit(student.group if student else "")
        form.addRow("Устойчивый ID:", self.id_edit)
        form.addRow("ФИО:", self.name_edit)
        form.addRow("Группа:", self.group_edit)
        layout.addLayout(form)
        layout.addWidget(_buttons(self))
        self.name_edit.setFocus()

    def values(self) -> dict[str, Any]:
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("Введите ФИО студента.")
        return {
            "student_id": self.id_edit.text().strip() or None,
            "full_name": name,
            "group": self.group_edit.text().strip(),
        }


def _student_text(student: Student) -> str:
    group = f" · {student.group}" if student.group else ""
    return f"{student.full_name}{group} · ID {student.id}"


class StudentSelectionDialog(QDialog):
    def __init__(
        self,
        students: list[Student],
        selected_ids: set[str],
        parent: QWidget | None = None,
        title: str = "Состав",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(660, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Отметьте ожидаемых участников. Поиск не сбрасывает выбор."))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по ФИО, группе или ID")
        layout.addWidget(self.search)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for student in sorted(students, key=lambda item: (item.full_name.casefold(), item.id)):
            item = QListWidgetItem(_student_text(student), self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, student.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if student.id in selected_ids else Qt.CheckState.Unchecked
            )
        layout.addWidget(self.list_widget, 1)
        selection = QHBoxLayout()
        select = QPushButton("Выбрать видимых")
        clear = QPushButton("Снять выбор видимых")
        select.clicked.connect(lambda: self._set_visible(Qt.CheckState.Checked))
        clear.clicked.connect(lambda: self._set_visible(Qt.CheckState.Unchecked))
        selection.addWidget(select)
        selection.addWidget(clear)
        self.count_label = _label()
        selection.addWidget(self.count_label, 1)
        layout.addLayout(selection)
        layout.addWidget(_buttons(self, "Применить состав"))
        self.search.textChanged.connect(self._filter)
        self.list_widget.itemChanged.connect(self._update_count)
        self._update_count()

    def _filter(self, text: str) -> None:
        text = text.casefold().strip()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item.setHidden(text not in item.text().casefold())

    def _set_visible(self, state: Qt.CheckState) -> None:
        self.list_widget.blockSignals(True)
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if not item.isHidden():
                item.setCheckState(state)
        self.list_widget.blockSignals(False)
        self._update_count()

    def _update_count(self, *_args: Any) -> None:
        self.count_label.setText(f"Выбрано: {len(self.selected_ids())}")

    def selected_ids(self) -> set[str]:
        return {
            str(self.list_widget.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.list_widget.count())
            if self.list_widget.item(index).checkState() == Qt.CheckState.Checked
        }


class ImportStudentsDialog(QDialog):
    def __init__(
        self, service: AttendanceService, files: CsvPort, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.files = files
        self._path: Path | None = None
        self._headers: list[str] = []
        self._rows: list[list[str]] = []
        self._students: tuple[Student, ...] = ()
        self.imported_count = 0
        self.updated_count = 0
        self.setWindowTitle("Импорт студентов из CSV")
        self.resize(850, 690)
        layout = QVBoxLayout(self)
        file_row = QHBoxLayout()
        self.path_label = _label("Файл не выбран")
        choose = QPushButton("Открыть CSV…")
        choose.clicked.connect(self._choose_file)
        file_row.addWidget(self.path_label, 1)
        file_row.addWidget(choose)
        layout.addLayout(file_row)
        options = QHBoxLayout()
        self.encoding = QComboBox()
        for label, name in (
            ("UTF-8 (с BOM или без)", "utf-8-sig"),
            ("Windows-1251", "cp1251"),
            ("UTF-16", "utf-16"),
        ):
            self.encoding.addItem(label, name)
        self.separator = QComboBox()
        for label, separator in (
            ("Точка с запятой ;", ";"),
            ("Запятая ,", ","),
            ("Табуляция", "\t"),
        ):
            self.separator.addItem(label, separator)
        options.addWidget(_label("Кодировка:"))
        options.addWidget(self.encoding)
        options.addWidget(_label("Разделитель:"))
        options.addWidget(self.separator)
        layout.addLayout(options)
        form = QFormLayout()
        self.id_column = QComboBox()
        self.name_column = QComboBox()
        self.group_column = QComboBox()
        form.addRow("ID студента:", self.id_column)
        form.addRow("ФИО:", self.name_column)
        form.addRow("Группа:", self.group_column)
        layout.addLayout(form)
        layout.addWidget(
            _label(
                "Первая строка CSV — заголовки. Существующие ID обновляются; "
                "ФИО не используется для автоматического объединения. "
                "Без ID создаются новые записи: сохраните выданные ID для повторного импорта."
            )
        )
        self.raw_table = QTableWidget()
        self.raw_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.raw_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.raw_table, 1)
        self.summary = _label("Выберите CSV, кодировку и соответствие колонок.")
        layout.addWidget(self.summary)
        self.errors = QPlainTextEdit()
        self.errors.setReadOnly(True)
        self.errors.setMaximumHeight(135)
        self.errors.setPlaceholderText("Ошибки проверки появятся здесь.")
        layout.addWidget(self.errors)
        self.buttons = _buttons(self, "Импортировать")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        layout.addWidget(self.buttons)
        self.encoding.currentIndexChanged.connect(self._reload)
        self.separator.currentIndexChanged.connect(self._reload)
        for combo in (self.id_column, self.name_column, self.group_column):
            combo.currentIndexChanged.connect(self._preview)

    def _choose_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Выберите список студентов", "", "CSV (*.csv *.tsv);;Все файлы (*)"
        )
        if filename:
            self.load_path(Path(filename))

    def load_path(self, path: Path) -> None:
        """Load a selected path, also useful for deterministic GUI verification."""
        self._path = path
        self.path_label.setText(str(path))
        self._reload()

    def _reload(self, *_args: Any) -> None:
        self._students = ()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        if self._path is None:
            return
        try:
            headers, rows = self.files.read_csv(
                self._path,
                encoding=str(self.encoding.currentData()),
                delimiter=str(self.separator.currentData()),
            )
        except Exception as exc:
            self._headers, self._rows = [], []
            self.raw_table.setRowCount(0)
            self.raw_table.setColumnCount(0)
            self.summary.setText(
                "Чтение не выполнено. Проверьте разделитель и выберите кодировку явно."
            )
            self.errors.setPlainText(str(exc))
            return
        self._headers, self._rows = list(headers), list(rows)
        self.raw_table.setColumnCount(len(headers))
        self.raw_table.setHorizontalHeaderLabels(headers)
        self.raw_table.setRowCount(min(len(rows), 200))
        for row_index, row in enumerate(rows[:200]):
            for column, value in enumerate(row[: len(headers)]):
                self.raw_table.setItem(row_index, column, QTableWidgetItem(str(value)))
        aliases = (
            {"id", "student_id", "student id", "ид", "идентификатор", "номер"},
            {"full_name", "fullname", "full name", "фио", "ф.и.о.", "имя", "name", "студент"},
            {"group", "группа", "группa", "student_group"},
        )
        for combo, names, field in zip(
            (self.id_column, self.name_column, self.group_column),
            aliases,
            ("id", "name", "group"),
            strict=True,
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Создать ID автоматически" if field == "id" else "Не выбрано", None)
            for index, header in enumerate(headers):
                combo.addItem(f"{index + 1}. {header}", index)
            match = next(
                (
                    index
                    for index, header in enumerate(headers)
                    if header.strip().casefold() in names
                ),
                None,
            )
            if match is not None:
                combo.setCurrentIndex(match + 1)
            combo.blockSignals(False)
        self._preview()

    def _preview(self, *_args: Any) -> None:
        self._students = ()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        if not self._headers:
            return
        try:
            preview: ImportPreview = self.files.preview_students(
                self._headers,
                self._rows,
                self.id_column.currentData(),
                self.name_column.currentData(),
                self.group_column.currentData(),
            )
            errors = list(preview.errors)
            existing_ids = {student.id for student in self.service.list_students()}
            self.updated_count = sum(student.id in existing_ids for student in preview.students)
            count = len(preview.students)
            self.summary.setText(
                f"Строк: {len(self._rows)}. Новых: {count - self.updated_count}. "
                f"Обновлений по ID: {self.updated_count}. Ошибок: {len(errors)}. "
                "Предпросмотр: первые 200 строк."
            )
            self.errors.setPlainText("\n".join(errors))
            if not errors and count:
                self._students = tuple(preview.students)
                self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        except Exception as exc:
            self.summary.setText("Проверка не завершена. Импорт не применён.")
            self.errors.setPlainText(str(exc))

    def accept(self) -> None:
        if not self._students:
            return
        try:
            self.service.import_students(list(self._students))
        except Exception as exc:
            _error(self, f"Импорт не применён: {exc}")
            return
        self.imported_count = len(self._students)
        super().accept()


class BindingDialog(QDialog):
    def __init__(
        self,
        card: CardInfo,
        students: list[Student],
        parent: QWidget | None = None,
        correction: bool = False,
    ) -> None:
        super().__init__(parent)
        self.card = card
        self.correction = correction
        self.setWindowTitle("Исправить ошибочную привязку" if correction else "Сопоставить карту")
        self.resize(660, 550)
        layout = QVBoxLayout(self)
        layout.addWidget(_label(f"Карта: {card.uid}"))
        current = (
            f"{card.full_name or 'ФИО не найдено'} (ID {card.student_id})"
            if card.student_id
            else "Не сопоставлена"
        )
        layout.addWidget(
            _label(f"Текущая привязка: {current}\nЗатрагиваемых занятий: {card.lesson_count}")
        )
        scope = (
            "Исправление изменит принадлежность всех прошлых отметок этой карты. "
            if correction
            else "Все сохранённые отметки этой карты будут сопоставлены выбранному студенту. "
        )
        layout.addWidget(_label(scope + "Исходные UID и время предъявлений сохраняются."))
        if correction:
            layout.addWidget(
                _label(
                    "Используйте исправление только для ошибочной привязки. "
                    "Передача той же карты другому студенту здесь не поддерживается."
                )
            )
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти студента по ФИО, группе или ID")
        layout.addWidget(self.search)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for student in sorted(students, key=lambda value: (value.full_name.casefold(), value.id)):
            item = QListWidgetItem(_student_text(student), self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, student.id)
        layout.addWidget(self.list_widget, 1)
        self.confirm = QCheckBox("Подтверждаю исправление принадлежности исторических отметок")
        self.confirm.setVisible(correction)
        layout.addWidget(self.confirm)
        self.buttons = _buttons(self, "Исправить привязку" if correction else "Сопоставить")
        layout.addWidget(self.buttons)
        self.search.textChanged.connect(self._filter)
        self.list_widget.itemSelectionChanged.connect(self._update_button)
        self.confirm.toggled.connect(self._update_button)
        if not students:
            layout.insertWidget(
                3, _label("Сначала добавьте студентов вручную или импортируйте CSV.")
            )
        self._update_button()

    def _filter(self, text: str) -> None:
        text = text.strip().casefold()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item.setHidden(text not in item.text().casefold())
            if item.isHidden():
                item.setSelected(False)
        self._update_button()

    def _update_button(self, *_args: Any) -> None:
        selected = self.list_widget.selectedItems()
        permitted = bool(selected) and (not self.correction or self.confirm.isChecked())
        if selected and self.correction:
            permitted = (
                permitted and selected[0].data(Qt.ItemDataRole.UserRole) != self.card.student_id
            )
        if self.card.student_id is not None and not self.correction:
            permitted = (
                bool(selected)
                and selected[0].data(Qt.ItemDataRole.UserRole) == self.card.student_id
            )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(permitted)

    def student_id(self) -> str:
        items = self.list_widget.selectedItems()
        if not items:
            raise ValueError("Выберите студента.")
        return str(items[0].data(Qt.ItemDataRole.UserRole))

    def accept(self) -> None:
        if self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            super().accept()


class ExportOptionsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Параметры CSV")
        self.resize(430, 160)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.separator = QComboBox()
        for label, separator in (
            ("Точка с запятой ;", ";"),
            ("Запятая ,", ","),
            ("Табуляция", "\t"),
        ):
            self.separator.addItem(label, separator)
        form.addRow("Разделитель:", self.separator)
        self.bom_box = QCheckBox("UTF-8 с BOM для табличных редакторов")
        self.bom_box.setChecked(True)
        form.addRow(self.bom_box)
        layout.addLayout(form)
        layout.addWidget(_buttons(self, "Продолжить"))

    @property
    def delimiter(self) -> str:
        return str(self.separator.currentData())

    @property
    def bom(self) -> bool:
        return self.bom_box.isChecked()
