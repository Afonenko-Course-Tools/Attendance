"""Separate private log: one row per card and lesson, with explicit UID export."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMainWindow,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.files import CsvPort
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import Lesson, LogRow, PublicResult
from attendance_lab.presentation.common import (
    actions,
    fill_table,
    guarded,
    local_time,
    plain_label,
    selected_key,
    table,
)
from attendance_lab.presentation.dialogs import ExportOptionsDialog


class _FeedbackDelegate(QStyledItemDelegate):
    """Keep scan colors visible on a selected row; outline still shows selection."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        colored = index.data(Qt.ItemDataRole.BackgroundRole) is not None
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if colored and selected:
            option = QStyleOptionViewItem(option)
            option.state &= ~(
                QStyle.StateFlag.State_Selected
                | QStyle.StateFlag.State_HasFocus
                | QStyle.StateFlag.State_MouseOver
            )
        super().paint(painter, option, index)
        if colored and selected:
            painter.save()
            painter.setPen(QColor("#1768b8"))
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()


class LogsWindow(QMainWindow):
    def __init__(
        self, service: AttendanceService, files: CsvPort, bind: Callable[[str, bool], None]
    ) -> None:
        super().__init__()
        self.service = service
        self.files = files
        self.bind = bind
        self.lesson: Lesson | None = None
        self.rows: list[LogRow] = []
        self._last_scan: tuple[int, str, PublicResult] | None = None
        self.setWindowTitle("Приватный журнал занятия — UID видны только преподавателю")
        self.resize(1100, 620)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 18, 18, 18)
        self.active = plain_label(wrap=True)
        self.active.setObjectName("ActiveBanner")
        self.context = plain_label(wrap=True)
        self.reader_state = plain_label(wrap=True)
        self.counts = plain_label()
        layout.addWidget(self.active)
        layout.addWidget(self.context)
        layout.addWidget(self.reader_state)
        layout.addWidget(self.counts)
        self.feedback = plain_label(wrap=True)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.log_table = table(
            ("Первое предъявление", "Последнее предъявление", "UID", "Студент", "Статус")
        )
        self.log_table.setItemDelegate(_FeedbackDelegate(self.log_table))
        layout.addWidget(self.log_table, 1)
        layout.addLayout(
            actions(
                ("Сопоставить выбранную карту", lambda: self._bind(False)),
                ("Исправить привязку…", lambda: self._bind(True)),
                ("Экспорт приватного журнала CSV…", self.export),
            )
        )
        self.setCentralWidget(central)

    def _bind(self, correction: bool) -> None:
        uid = selected_key(self.log_table)
        if uid is not None:
            self.bind(str(uid), correction)

    def set_context(
        self, lesson: Lesson | None, label: str, active_label: str, reader_status: str
    ) -> None:
        self.lesson = lesson
        if lesson is None:
            self._last_scan = None
        self.context.setText("Просматриваемый журнал: " + (label or "занятие не выбрано"))
        self.active.setText("Активная регистрация: " + (active_label or "закрыта"))
        self.reader_state.setText("Считыватель: " + reader_status)
        self.refresh()

    def refresh(self) -> None:
        self.rows = self.service.lesson_log(self.lesson.id) if self.lesson else []
        timezone = self.lesson.timezone if self.lesson else None
        fill_table(
            self.log_table,
            [
                (
                    local_time(row.first_seen, timezone),
                    local_time(row.last_seen, timezone),
                    row.uid,
                    row.full_name or "Не сопоставлена",
                    "Не сопоставлена"
                    if not row.student_id
                    else ("Вне ожидаемого состава" if row.outside_roster else "Сопоставлена"),
                )
                for row in self.rows
            ],
            [row.uid for row in self.rows],
        )
        if self.lesson:
            counts = self.service.lesson_counts(self.lesson.id)
            self.counts.setText(
                f"Уникальных карт: {counts.unique_cards} · "
                f"Распознано студентов: {counts.identified_students} · "
                f"Без сопоставления: {counts.unidentified_cards} · "
                f"Ожидаемый состав: {counts.expected_students}"
            )
        else:
            self.counts.clear()
        self._apply_feedback()

    def show_scan(self, lesson_id: int, uid: str, result: PublicResult) -> None:
        """Highlight the latest committed attempt, without adding a log row."""
        self._last_scan = (lesson_id, uid, result)
        self.refresh()

    def _apply_feedback(self) -> None:
        self.feedback.hide()
        if not self.lesson or not self._last_scan or self._last_scan[0] != self.lesson.id:
            return
        _, uid, result = self._last_scan
        if result.kind not in ("success", "repeat"):
            return
        repeated = result.kind == "repeat"
        background, foreground = ("#fef3c7", "#713f12") if repeated else ("#dcfce7", "#14532d")
        message = "Повторная отметка на этом занятии" if repeated else "Первая отметка сохранена"
        self.feedback.setText("Последнее предъявление: " + message)
        self.feedback.setStyleSheet(
            f"background: {background}; color: {foreground}; padding: 12px; "
            "border-radius: 6px; font-weight: 600;"
        )
        self.feedback.show()
        for index, row in enumerate(self.rows):
            if row.uid == uid:
                for column in range(self.log_table.columnCount()):
                    item = self.log_table.item(index, column)
                    if item is not None:
                        item.setBackground(QColor(background))
                        item.setForeground(QColor(foreground))
                        item.setToolTip(message)
                break

    def export(self) -> None:
        if self.lesson is None:
            return
        options = ExportOptionsDialog(self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Приватный экспорт с UID", f"private-card-log-{self.lesson.id}.csv", "CSV (*.csv)"
        )
        if filename:
            lesson, rows = self.lesson, list(self.rows)
            guarded(
                self,
                lambda: self.files.export_log(
                    Path(filename), lesson, rows, delimiter=options.delimiter, bom=options.bom
                ),
            )
