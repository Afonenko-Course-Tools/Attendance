"""Exact-cell historical corrections with stable student and pair identities."""

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.presentation.attendance_models import IDENTITY, PAIR, SORT, StudentProxy
from attendance_lab.presentation.common import actions, guarded, plain_label
from attendance_lab.presentation.preparation import FormDialog, checked, choices
from attendance_lab.presentation.sorting import sort_dialog


class HistoryModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.report = None
        self.contexts = {}
        self.pair_blocks = {}
        self.pair_contexts = {}

    def set_report(self, report):
        self.beginResetModel()
        self.report = report
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: B008
        return len(self.report.rows) if self.report and not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()):  # noqa: B008
        return 7 + len(self.report.lessons) if self.report else 7

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        if section < 7:
            return ("ФИО", "Группа", "Курс", "Специальность", "Подгруппа", "Зачётная книжка", "ID")[
                section
            ]
        if self.report is None:
            return None
        lesson = self.report.lessons[section - 7]
        return f"{lesson.starts_at.astimezone(ZoneInfo(lesson.timezone)):%d.%m.%Y}\n{self.pair_blocks.get(lesson.id, lesson.title)}\nПара #{lesson.id}"

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not self.report:
            return None
        row = self.report.rows[index.row()]
        column = index.column()
        if role == IDENTITY:
            return row.student.id
        if role == PAIR:
            return self.report.lessons[column - 7].id if column >= 7 else None
        if role == Qt.ItemDataRole.ToolTipRole:
            context = (
                self.contexts.get(row.student.id)
                if column < 7
                else self.pair_contexts.get((row.student.id, self.report.lessons[column - 7].id))
            )
            if context:
                return f"Сохранённый контекст {'последней пары периода' if column < 7 else 'этой пары'}: курс {context.course} · группа {context.group_number} · {context.specialty} · {context.subgroup or '—'}"
            return "Сохранённого академического контекста нет"
        if role in (Qt.ItemDataRole.DisplayRole, SORT):
            if column < 7:
                context = self.contexts.get(row.student.id)
                value = (
                    row.student.full_name,
                    getattr(context, "group_number", None) or row.student.group,
                    getattr(context, "course", None) or 0,
                    getattr(context, "specialty", ""),
                    getattr(context, "subgroup", ""),
                    getattr(context, "record_book_number", ""),
                    row.student.id,
                )[column]
                if role == SORT:
                    if column == 1:
                        try:
                            return int(value)
                        except ValueError, TypeError:
                            return 0
                    return value
                return str(value) if value not in ("", None, 0) else "—"
            cell = row.cells[column - 7]
            label = {
                "present": "✓ Присутствует",
                "missing": "Нет отметки",
                "outside": "— Вне состава",
                "expected": "Запланировано",
                "pending": "Регистрация",
            }[cell.state]
            if cell.outside_roster:
                label += " · вне состава"
            if cell.first_seen:
                label += (
                    " · ручное подтверждение " if cell.manual_confirmation else " · карта "
                ) + cell.first_seen.astimezone(
                    ZoneInfo(self.report.lessons[column - 7].timezone)
                ).strftime("%H:%M")
            return label
        return None

    def valid_target(self, index):
        if index.column() < 7 or not self.report:
            return None
        lesson = self.report.lessons[index.column() - 7]
        row = self.report.rows[index.row()]
        if lesson.starts_at > datetime.now(UTC) or row.cells[index.column() - 7].state == "outside":
            return None
        return row.student.id, lesson.id


class HistoryPane(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.discipline, self.search = QComboBox(), QLineEdit()
        self.search.setPlaceholderText("Фильтр ФИО / группы · F3")
        self.date_from, self.date_to = (
            QDateEdit(QDate.currentDate().addMonths(-6)),
            QDateEdit(QDate.currentDate()),
        )
        for control in (self.date_from, self.date_to):
            control.setCalendarPopup(True)
        for control in (self.discipline, self.date_from, self.date_to, self.search):
            bar.addWidget(control)
        layout.addLayout(bar)
        self.model, self.proxy = HistoryModel(), StudentProxy()
        self.proxy.setSourceModel(self.model)
        self.proxy.book_column = 5
        self.student_cache = {}
        self.evidence_changed = lambda: None
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.matrix = QTableView()
        self.matrix.setModel(self.proxy)
        self.matrix.setSortingEnabled(True)
        self.matrix.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.matrix.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.names = QTableView()
        self.names.setModel(self.proxy)
        self.names.setSelectionModel(self.matrix.selectionModel())
        self.names.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.names.setFixedWidth(235)
        self.names.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.names.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.names.verticalHeader().hide()
        self.names.horizontalHeader().setFixedHeight(70)
        self.matrix.horizontalHeader().setFixedHeight(70)
        self.names.verticalScrollBar().valueChanged.connect(
            self.matrix.verticalScrollBar().setValue
        )
        self.matrix.verticalScrollBar().valueChanged.connect(
            self.names.verticalScrollBar().setValue
        )
        tables = QHBoxLayout()
        tables.setSpacing(0)
        tables.addWidget(self.names)
        tables.addWidget(self.matrix, 1)
        layout.addLayout(tables, 1)
        self.show_context = QCheckBox("Показать академический контекст периода")
        self.show_context.toggled.connect(self.toggle_context)
        layout.addWidget(self.show_context)
        self.selection_count = plain_label(
            "Выберите ячейки посещаемости; Shift — прямоугольный диапазон."
        )
        layout.addWidget(self.selection_count)
        layout.addLayout(
            actions(
                ("Отметить выбранные ячейки…", self.manual),
                ("Отменить ручную пачку…", self.revoke),
                ("Исправить состав пары…", self.adjust_roster),
                ("Обновить", self.load),
                ("Сортировка…", lambda: sort_dialog(self, self.model, self.proxy, 7)),
            )
        )
        self.matrix.selectionModel().selectionChanged.connect(self.update_count)
        self.search.textChanged.connect(self.filter)
        self.discipline.currentIndexChanged.connect(self.load)
        self.date_from.dateChanged.connect(self.load)
        self.date_to.dateChanged.connect(self.load)

    def refresh(self):
        selected = self.discipline.currentData()
        self.discipline.blockSignals(True)
        self.discipline.clear()
        for item in self.service.list_disciplines():
            self.discipline.addItem(item.name, item.id)
        self.discipline.setCurrentIndex(max(0, self.discipline.findData(selected)))
        self.discipline.blockSignals(False)
        self.load()

    def load(self, *_):
        self.matrix.clearSelection()
        self.student_cache = {s.id: s for s in self.service.list_students()}
        discipline = self.discipline.currentData()
        report = (
            self.service.report(
                discipline,
                date_from=self.date_from.date().toPython(),
                date_to=self.date_to.date().toPython(),
            )
            if discipline is not None
            else None
        )
        self.refresh_contexts(report)
        self.model.set_report(report)
        for column in range(self.model.columnCount()):
            self.matrix.setColumnWidth(column, 225 if column == 0 else 150)
        self.matrix.setColumnHidden(0, True)
        for column in range(1, self.model.columnCount()):
            self.names.setColumnHidden(column, True)
        self.names.setColumnWidth(0, 230)
        self.toggle_context(self.show_context.isChecked())
        self.update_count()

    def refresh_contexts(self, report):
        discipline = self.discipline.currentData()
        self.model.contexts = {}
        future_contexts = {}
        self.model.pair_contexts = {}
        if report:
            for lesson in sorted(report.lessons, key=lambda p: p.starts_at):
                for context in self.service.redesign.get_pair_contexts(lesson.id):
                    target_contexts = (
                        self.model.contexts if lesson.roster_fixed else future_contexts
                    )
                    target_contexts[context.student_id] = context
                    self.model.pair_contexts[(context.student_id, lesson.id)] = context
        for student_id, context in future_contexts.items():
            self.model.contexts.setdefault(student_id, context)
        self.model.pair_blocks = (
            {
                pair: f"Блок #{block.id} · {offset + 1}/{len(block.pair_ids)}"
                for block in self.service.redesign.list_blocks(discipline_id=discipline)
                for offset, pair in enumerate(block.pair_ids)
            }
            if discipline is not None
            else {}
        )

    def toggle_context(self, visible):
        for column in range(2, 7):
            self.matrix.setColumnHidden(column, not visible)

    def filter(self, text):
        self.matrix.clearSelection()
        self.proxy.setFilterFixedString(text)
        self.update_count()

    def targets(self):
        return {
            target
            for index in self.matrix.selectionModel().selectedIndexes()
            if (target := self.model.valid_target(self.proxy.mapToSource(index))) is not None
        }

    def update_count(self, *_):
        targets = self.targets()
        self.selection_count.setText(
            f"Студентов: {len({s for s, _ in targets})} · пар: {len({p for _, p in targets})} · допустимых отметок: {len(targets)}"
        )

    def manual(self):
        targets = self.targets()
        if not targets or self.model.report is None:
            return
        names = {r.student.id: r.student.full_name for r in self.model.report.rows}
        pairs = {p.id: p for p in self.model.report.lessons}
        preview = "\n".join(
            f"{names[s]} · {pairs[p].starts_at:%d.%m.%Y %H:%M} · пара #{p}"
            for s, p in sorted(targets)
        )
        reason, ok = QInputDialog.getMultiLineText(
            self,
            "Проверка точных получателей",
            f"{preview}\n\nОтметок: {len(targets)}. Укажите причину:",
        )
        if ok:
            guarded(
                self,
                lambda: (
                    self.service.redesign.create_manual_batch(targets, reason),
                    self.update_evidence(),
                    self.evidence_changed(),
                ),
            )

    def update_evidence(self, pair_ids=None):
        """Project authoritative cells, retaining source order and persistent indexes."""
        previous = self.model.report
        discipline = self.discipline.currentData()
        if previous is None or discipline is None:
            return
        fresh = self.service.report(
            discipline,
            date_from=self.date_from.date().toPython(),
            date_to=self.date_to.date().toPython(),
        )
        if tuple(p.id for p in fresh.lessons) != tuple(p.id for p in previous.lessons):
            self.load()
            return
        old_contexts = (self.model.contexts, self.model.pair_contexts)
        self.refresh_contexts(fresh)
        contexts_changed = old_contexts != (self.model.contexts, self.model.pair_contexts)
        fresh_by_id = {row.student.id: row for row in fresh.rows}
        rows = list(previous.rows)
        for number in reversed(range(len(rows))):
            if rows[number].student.id not in fresh_by_id:
                self.model.beginRemoveRows(QModelIndex(), number, number)
                rows.pop(number)
                self.model.report = replace(fresh, rows=tuple(rows))
                self.model.endRemoveRows()
        existing = {row.student.id for row in rows}
        additions = [row for row in fresh.rows if row.student.id not in existing]
        if additions:
            self.model.beginInsertRows(QModelIndex(), len(rows), len(rows) + len(additions) - 1)
            rows.extend(additions)
            self.model.report = replace(fresh, rows=tuple(rows))
            self.model.endInsertRows()
        changed = []
        for number, row in enumerate(rows):
            replacement = fresh_by_id[row.student.id]
            if replacement != row or contexts_changed:
                rows[number] = replacement
                changed.append(number)
        self.model.report = replace(fresh, rows=tuple(rows))
        for number in changed:
            self.model.dataChanged.emit(
                self.model.index(number, 0),
                self.model.index(number, self.model.columnCount() - 1),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, SORT],
            )
        if previous.lessons != fresh.lessons:
            self.model.headerDataChanged.emit(
                Qt.Orientation.Horizontal, 7, self.model.columnCount() - 1
            )

    def adjust_roster(self):
        pairs = {
            index.data(PAIR)
            for index in self.matrix.selectionModel().selectedIndexes()
            if index.data(PAIR) is not None
        }
        if len(pairs) != 1:
            self.selection_count.setText("Для коррекции состава выберите ячейку одной пары.")
            return
        pair = next(iter(pairs))
        api = self.service.redesign
        original = {s.student_id for s in api.get_pair_contexts(pair)}
        dialog = FormDialog(f"Историческая коррекция состава пары #{pair}", self)
        students = choices(
            [
                (f"{s.full_name} · {s.record_book_number} · гр. {s.group_number}", s.student_id)
                for s in api.list_student_contexts()
            ],
            original,
        )
        reason = QLineEdit()
        dialog.form.addRow("Состав пары (основания присутствия сохраняются)", students)
        dialog.form.addRow("Обязательная причина", reason)
        dialog.finish()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            guarded(
                self,
                lambda: (api.set_pair_roster(pair, checked(students), reason.text()), self.load()),
            )

    def revoke(self):
        batches = [b for b in self.service.redesign.list_manual_batches() if b.revoked_at is None]
        labels = [
            f"#{b.id} · {b.created_at:%d.%m %H:%M} · {len(b.targets)} отметок · {b.reason}"
            for b in batches
        ]
        label, ok = QInputDialog.getItem(
            self, "Отмена ручной пачки", "Карточные основания сохранятся:", labels, 0, False
        )
        if ok and label in labels:
            guarded(
                self,
                lambda: (
                    self.service.redesign.revoke_manual_batch(batches[labels.index(label)].id),
                    self.update_evidence(),
                    self.evidence_changed(),
                ),
            )
