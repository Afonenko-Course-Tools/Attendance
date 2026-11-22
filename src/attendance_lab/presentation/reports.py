"""Attendance projection and explicit, filtered exports."""

from dataclasses import replace
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.files import CsvPort
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceReport
from attendance_lab.presentation.common import (
    actions,
    fill_table,
    filter_table,
    guarded,
    lesson_label,
    local_time,
    plain_label,
    search_field,
    table,
)
from attendance_lab.presentation.dialogs import ExportOptionsDialog
from attendance_lab.presentation.table_columns import TableColumns


class ReportsPane(QWidget):
    def __init__(
        self, service: AttendanceService, files: CsvPort, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.files = files
        self.current_report: AttendanceReport | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        self.discipline = QComboBox()
        self.discipline.setMinimumWidth(260)
        self.group = QComboBox()
        self.group.setMinimumWidth(140)
        self.student = QComboBox()
        self.student.setMinimumWidth(220)
        filters = QHBoxLayout()
        for label, control in (
            ("Дисциплина", self.discipline),
            ("Группа", self.group),
            ("Студент", self.student),
        ):
            filters.addWidget(plain_label(label))
            filters.addWidget(control, 1)
        layout.addLayout(filters)
        period = QHBoxLayout()
        self.use_dates = QCheckBox("Период")
        self.date_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.date_to = QDateEdit(QDate.currentDate().addMonths(1))
        for control in (self.date_from, self.date_to):
            control.setCalendarPopup(True)
            control.setDisplayFormat("dd.MM.yyyy")
            control.setEnabled(False)
        period.addWidget(self.use_dates)
        period.addWidget(self.date_from)
        period.addWidget(plain_label("—"))
        period.addWidget(self.date_to)
        period.addStretch()
        layout.addLayout(period)
        self.notice = plain_label(wrap=True)
        self.notice.setObjectName("Muted")
        layout.addWidget(self.notice)
        self.search = search_field("ФИО, группа или ID студента")
        layout.addWidget(self.search)
        self.matrix = table(("ID студента", "ФИО", "Группа"), sortable=True)
        self.columns = TableColumns(self.matrix, "attendance-report", keys=self.column_keys)
        self.search.textChanged.connect(self.filter_rows)
        self.matrix.model().layoutChanged.connect(self.filter_rows)
        layout.addWidget(self.matrix, 1)
        legend = plain_label(
            "Присутствовал — сохранено предъявление  ·  Нет отметки — занятие завершено  ·  "
            "Ожидается — занятие запланировано  ·  Пока нет отметки — регистрация открыта  ·  — — вне состава",
            wrap=True,
        )
        legend.setObjectName("Muted")
        layout.addWidget(legend)
        layout.addLayout(
            actions(
                ("Обновить", self.refresh_report),
                ("Экспорт таблицы CSV", lambda: self.export("matrix")),
                ("Экспорт присутствий CSV", lambda: self.export("presences")),
            )
        )
        for control in (self.discipline, self.group, self.student):
            control.currentIndexChanged.connect(lambda *_: self.refresh_report())
        self.use_dates.toggled.connect(self._toggle_dates)
        self.date_from.dateChanged.connect(lambda *_: self.refresh_report())
        self.date_to.dateChanged.connect(lambda *_: self.refresh_report())

    def _toggle_dates(self, enabled: bool) -> None:
        self.date_from.setEnabled(enabled)
        self.date_to.setEnabled(enabled)
        self.refresh_report()

    def refresh(self) -> None:
        students = self.service.list_students()
        entries = (
            (self.discipline, [(d.name, d.id) for d in self.service.list_disciplines()]),
            (
                self.group,
                [("Все группы", "")]
                + [(g, g) for g in sorted({s.group for s in students if s.group})],
            ),
            (
                self.student,
                [("Все студенты", None)]
                + [(f"{s.full_name} · {s.group} · {s.id}", s.id) for s in students],
            ),
        )
        for control, values in entries:
            selected = control.currentData()
            control.blockSignals(True)
            control.clear()
            for label, key in values:
                control.addItem(label, key)
            index = control.findData(selected)
            control.setCurrentIndex(index if index >= 0 else (0 if values else -1))
            control.blockSignals(False)
        self.refresh_report()

    def refresh_report(self) -> None:
        discipline_id = self.discipline.currentData()
        if discipline_id is None:
            self.current_report = None
            self.matrix.setRowCount(0)
            self.notice.setText("Создайте дисциплину, чтобы увидеть таблицу посещаемости.")
            return
        if self.use_dates.isChecked() and self.date_from.date() > self.date_to.date():
            self.current_report = None
            self.matrix.setRowCount(0)
            self.notice.setText("Начало периода должно быть не позже окончания.")
            return

        def get_date(control: QDateEdit) -> date:
            value = control.date()
            return date(value.year(), value.month(), value.day())

        report = self.service.report(
            discipline_id,
            date_from=get_date(self.date_from) if self.use_dates.isChecked() else None,
            date_to=get_date(self.date_to) if self.use_dates.isChecked() else None,
            group=self.group.currentData() or "",
            student_id=self.student.currentData(),
        )
        self.current_report = report
        headers = ["ID студента", "ФИО", "Группа"]
        headers.extend(
            lesson.starts_at.astimezone(ZoneInfo(lesson.timezone)).strftime("%d.%m.%Y\n%H:%M")
            + (f"\n{lesson.title}" if lesson.title else "")
            + f"\n#{lesson.id}"
            for lesson in report.lessons
        )
        self.matrix.setColumnCount(len(headers))
        self.matrix.setHorizontalHeaderLabels(headers)
        labels = {
            "present": "Присутствовал",
            "missing": "Нет отметки",
            "expected": "Ожидается",
            "pending": "Пока нет отметки",
            "outside": "—",
        }
        colors = {
            "present": "#dff3e6",
            "missing": "#fff0dc",
            "expected": "#edf2fa",
            "pending": "#fff7d6",
            "outside": "#ffffff",
        }
        rows = []
        for row in report.rows:
            values = [row.student.id, row.student.full_name, row.student.group]
            for cell, lesson in zip(row.cells, report.lessons, strict=True):
                label = labels[cell.state]
                if cell.first_seen:
                    label += " " + cell.first_seen.astimezone(ZoneInfo(lesson.timezone)).strftime(
                        "%H:%M"
                    )
                if cell.outside_roster:
                    label += " *"
                values.append(label)
            rows.append(values)
        fill_table(self.matrix, rows, [row.student.id for row in report.rows], restore_sort=False)
        for i, row in enumerate(report.rows):
            for j, cell in enumerate(row.cells):
                item = self.matrix.item(i, j + 3)
                assert item is not None
                item.setBackground(QColor(colors[cell.state]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(
                    (
                        local_time(cell.first_seen, report.lessons[j].timezone)
                        if cell.first_seen
                        else labels[cell.state]
                    )
                    + ("\nПосещение вне ожидаемого состава" if cell.outside_roster else "")
                )
        for index, lesson in enumerate(report.lessons):
            header = self.matrix.horizontalHeaderItem(index + 3)
            assert header is not None
            header.setToolTip(lesson_label(lesson))
        self.columns.restore_visibility()
        self.matrix.setSortingEnabled(True)
        self.filter_rows()
        warning = (
            f"Несопоставленных карт: {report.unknown_count}. Сопоставление не завершено; таблица может измениться. "
            if report.unknown_count
            else ""
        )
        self.notice.setText(
            warning
            + f"Студентов: {len(report.rows)} · Занятий: {len(report.lessons)}. * — посещение вне состава."
        )

    def column_keys(self):
        lessons = self.current_report.lessons if self.current_report else ()
        return ("id", "student", "group", *(f"pair-{lesson.id}" for lesson in lessons))

    def filter_rows(self, *_):
        filter_table(self.matrix, self.search.text(), columns=(0, 1, 2))

    def export(self, kind: str) -> None:
        if self.current_report is None:
            return
        options = ExportOptionsDialog(self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Экспорт посещаемости", f"attendance-{kind}.csv", "CSV (*.csv)"
        )
        if filename:
            snapshot = self.current_report
            if self.search.text().strip():
                visible = {
                    item.data(Qt.ItemDataRole.UserRole)
                    for row in range(self.matrix.rowCount())
                    if not self.matrix.isRowHidden(row)
                    and (item := self.matrix.item(row, 0)) is not None
                }
                snapshot = replace(
                    snapshot, rows=tuple(row for row in snapshot.rows if row.student.id in visible)
                )
            guarded(
                self,
                lambda: self.files.export_report(
                    Path(filename),
                    snapshot,
                    kind=kind,
                    delimiter=options.delimiter,
                    bom=options.bom,
                ),
            )
