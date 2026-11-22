"""Report cards, original written scores and course-wide calculation preview."""

from collections.abc import Mapping
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.grading_preview import (
    CalculationDraft,
    GradingFitRequest,
    GradingPreviewRequest,
    GradingPreviewService,
    ReportCardDraft,
    preview_report_card,
)
from attendance_lab.application.list_queries import (
    StudentSearch,
    filter_report_rows,
    report_sort_value,
)
from attendance_lab.domain.grading import (
    CRITERIA,
    FitBound,
    ReportCard,
    WrittenScore,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.presentation.common import (
    STYLE,
    TableCell,
    TableValue,
    WidgetZoom,
    bind_table_clear,
    fill_table,
    guarded,
    plain_label,
    search_field,
    selected_key,
    table,
)
from attendance_lab.presentation.interface_zoom import InterfaceZoom
from attendance_lab.presentation.lesson_picker import LessonPicker
from attendance_lab.presentation.report_criteria import ReportCriteriaPanel
from attendance_lab.presentation.report_topic_picker import ReportTopicPicker
from attendance_lab.presentation.table_columns import TableColumns
from attendance_lab.presentation.toolbars import office_toolbar, tool_action, tool_menu


class ReportCardDialog(QDialog):
    def __init__(self, workflow, course_id, student_id, lesson_id=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setStyleSheet(STYLE)
        self.workflow = workflow
        self.course_id, self.student_id = course_id, student_id
        student = next(
            (s for s in workflow.students(course_id) if s.student_id == student_id), None
        )
        self.setWindowTitle("Доклад — " + (student.full_name if student else "карточка оценивания"))
        available = self.screen().availableGeometry()
        self.resize(min(840, available.width()), min(800, available.height()))
        layout = QVBoxLayout(self)
        fields = QWidget()
        form = QFormLayout(fields)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.topic = ReportTopicPicker(workflow.report_topic_catalog(course_id))
        self.lesson = LessonPicker()
        self.written = QCheckBox("Реферат представлен")
        card = workflow.report(course_id, student_id)
        if card:
            self.topic.setCurrentText(card.topic)
            lesson_id = card.lesson_id
        else:
            self.topic.setCurrentText("")
        self.lesson.replace_choices(
            (
                lesson
                for lesson in workflow.lesson_choices(course_id, include_deleted=bool(card))
                if not lesson.deleted or (card is not None and lesson.id == card.lesson_id)
            ),
            lesson_id,
        )
        self.lesson.setEnabled(card is None)
        change = QPushButton("Изменить занятие сдачи…")
        change.setVisible(card is not None)
        change.clicked.connect(self.edit_lesson)
        form.addRow("Тема реферата", self.topic)
        form.addRow("Занятие сдачи", self.lesson)
        form.addRow("", change)
        form.addRow("", self.written)
        self.criteria = ReportCriteriaPanel(
            card.levels if card else None, card.written if card else False, header=fields
        )
        self.sliders = self.criteria.controls
        self.criteria.levelsChanged.connect(self.update_preview)
        layout.addWidget(self.criteria, 1)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить карточку")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(lambda: guarded(self, self.save))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.written.setChecked(card.written if card else False)
        self.written.toggled.connect(self.update_preview)
        self.lesson.currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def edit_lesson(self):
        self.lesson.setEnabled(True)
        self.lesson.open_picker()

    def update_preview(self, *_):
        self.criteria.set_written(self.written.isChecked())
        draft = ReportCardDraft(
            self.course_id,
            self.student_id,
            self.lesson.currentData(),
            self.topic.currentText(),
            self.written.isChecked(),
            tuple(s.value() for s in self.sliders),
        )
        result = preview_report_card(draft, self.workflow.parameters(self.course_id))
        self.preview.setText(f"Предварительная оценка: {result.grade:.2f} / 10")

    def save(self):
        lesson_id = self.lesson.currentData()
        if lesson_id is None:
            raise AttendanceError("Выберите конкретное занятие сдачи.")
        self.workflow.save_report(
            ReportCard(
                self.course_id,
                self.student_id,
                lesson_id,
                self.topic.currentText(),
                self.written.isChecked(),
                tuple(s.value() for s in self.sliders),
            )
        )
        self.accept()


class HistogramWidget(QWidget):
    """Paired frequencies on the actual discrete 0–10 recommendation scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current = self.preview_counts = (0,) * 11
        self.setMinimumHeight(130)
        self.setAccessibleName("Распределение итоговых отметок: применено и предпросмотр")

    def set_distributions(self, current, preview):
        self.current, self.preview_counts = current, preview
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        blue, amber = QColor("#003e78"), QColor("#b36700")
        painter.fillRect(self.rect(), self.palette().base())
        painter.setPen(self.palette().text().color())
        painter.drawText(12, 17, "Число студентов")
        for x, color, label in ((150, blue, "Применено"), (290, amber, "Предпросмотр")):
            painter.fillRect(x, 7, 12, 12, color)
            painter.drawText(x + 18, 18, label)
        left, right, top, bottom = 25.0, float(self.width() - 12), 38.0, float(self.height() - 24)
        maximum = max(1, *self.current, *self.preview_counts)
        step = (right - left) / 11
        for index, (current, preview) in enumerate(
            zip(self.current, self.preview_counts, strict=True)
        ):
            center = left + (index + 0.5) * step
            bar_width = max(3.0, min(20.0, step / 3))
            for offset, count, color in ((-bar_width, current, blue), (1.0, preview, amber)):
                height = (bottom - top) * count / maximum
                painter.fillRect(
                    QRectF(center + offset, bottom - height, bar_width - 1, height), color
                )
                if count:
                    painter.setPen(color)
                    painter.drawText(
                        QRectF(center + offset - 6, bottom - height - 16, bar_width + 12, 16),
                        Qt.AlignmentFlag.AlignCenter,
                        str(count),
                    )
            painter.setPen(self.palette().text().color())
            painter.drawText(
                QRectF(center - 14, bottom + 3, 28, 18), Qt.AlignmentFlag.AlignCenter, str(index)
            )
        painter.setPen(self.palette().mid().color())
        painter.drawLine(int(left), int(bottom), int(right), int(bottom))
        painter.end()


def grade_cell(value, student_id, order):
    item = TableCell(TableValue("" if value is None else str(value), order))
    item.setData(Qt.ItemDataRole.UserRole, student_id)
    item.setToolTip(item.text())
    return item


class GradingPane(QWidget):
    def __init__(self, service, files=None, parent=None):
        super().__init__(parent)
        self.files = files
        self.service = service
        self.workflow = service.grading
        self.previews = GradingPreviewService(self.workflow)
        self._preview_revision = 0
        self.course_id = self.lesson_id = None
        self._loading = False
        self._loaded_context = None
        self.report_dialog: ReportCardDialog | None = None
        self.interface_zoom: InterfaceZoom | None = None
        self._work_ids = []
        self._reports_enabled = False
        self._report_rows = []
        self._written_scores: Mapping[tuple[str, int], float] = {}
        self._written_variants: Mapping[tuple[str, int], str] = {}
        self._draft_variants: dict[tuple[str, int], str] = {}
        self._variant_metric: int | None = None
        layout = QVBoxLayout(self)
        self.notice = QLabel("Выберите курс.")
        self.notice.setWordWrap(True)
        notice_row = QHBoxLayout()
        notice_row.addWidget(self.notice, 1)
        self.members_button = QPushButton("Добавить студентов…")
        self.members_button.clicked.connect(self.open_members)
        self.members_button.hide()
        notice_row.addWidget(self.members_button)
        layout.addLayout(notice_row)
        self.setup_tools = self._toolbar(self)
        self.enable_reports_action = self._action(
            self.setup_tools,
            "Включить доклады",
            QStyle.StandardPixmap.SP_DialogApplyButton,
            self.enable_reports,
        )
        self.add_work_action = self._action(
            self.setup_tools,
            "Добавить контрольную",
            QStyle.StandardPixmap.SP_FileIcon,
            self.add_work,
        )
        self.export_action = self._action(
            self.setup_tools, "Экспорт XLSX", QStyle.StandardPixmap.SP_DialogSaveButton, self.export
        )
        layout.addWidget(self.setup_tools)
        self.search = search_field(
            "ФИО, группа или № зачётки · F3", name="Поиск студента в оценках"
        )
        self._student_search = StudentSearch(())
        self.search.textChanged.connect(self.filter_students)
        layout.addWidget(self.search)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.empty = QWidget()
        layout.addWidget(self.empty, 1)
        reports = QWidget()
        report_layout = QVBoxLayout(reports)
        report_tools = self._toolbar(reports)
        self.open_report_action = self._action(
            report_tools,
            "Оценить доклад",
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            self.open_report,
        )
        columns_menu = tool_menu(report_tools, "Столбцы")
        report_layout.addWidget(report_tools)
        filters = QHBoxLayout()
        self.report_group = QComboBox()
        self.report_group.setMinimumContentsLength(8)
        self.report_group.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.report_status = QComboBox()
        self.report_lesson = LessonPicker(allow_all=True)
        self.report_lesson.setAccessibleName("Фильтр по занятию сдачи")
        for title, control in (
            ("Группа", self.report_group),
            ("Статус", self.report_status),
            ("Занятие сдачи", self.report_lesson),
        ):
            filters.addWidget(QLabel(title))
            filters.addWidget(control, int(control is self.report_lesson))
        for label, key in (("Все", None), ("Не оценены", False), ("Оценены", True)):
            self.report_status.addItem(label, key)
        clear_filters = QPushButton("Сбросить")
        clear_filters.clicked.connect(self.clear_report_filters)
        filters.addWidget(clear_filters)
        report_layout.addLayout(filters)
        self.report_count = QLabel()
        report_layout.addWidget(self.report_count)
        self.report_table = QTableWidget()
        self.report_table.verticalHeader().hide()
        self.report_table.setAlternatingRowColors(True)
        self.report_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.report_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.report_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.report_table.itemSelectionChanged.connect(self.update_actions)
        self.report_table.itemDoubleClicked.connect(lambda *_: guarded(self, self.open_report))
        self.report_columns = TableColumns(
            self.report_table,
            "reports",
            columns_menu,
            keys=("student", "group", "topic", "lesson", "grade"),
        )
        self.report_table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.report_table.model().layoutChanged.connect(self.filter_students)
        for control in (self.report_group, self.report_status, self.report_lesson):
            control.currentIndexChanged.connect(self.filter_students)
        self.report_shortcuts = []
        for key in ("Return", "Enter"):
            shortcut = QShortcut(QKeySequence(key), self.report_table)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda: guarded(self, self.open_report))
            self.report_shortcuts.append(shortcut)
        report_layout.addWidget(self.report_table, 1)
        self.tabs.addTab(reports, "Доклады")
        written = QWidget()
        written_layout = QVBoxLayout(written)
        written_tools = self._toolbar(written)
        self.paste_action = self._action(
            written_tools,
            "Вставить баллы",
            QStyle.StandardPixmap.SP_FileDialogListView,
            self.paste_scores,
        )
        work_columns_menu = tool_menu(written_tools, "Столбцы")
        written_layout.addWidget(written_tools)
        self.work_notice = QLabel()
        self.work_notice.setWordWrap(True)
        self.work_notice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        written_layout.addWidget(self.work_notice)
        self.journal = QTableWidget()
        self.journal.verticalHeader().hide()
        self.journal.setAlternatingRowColors(True)
        self.journal.itemChanged.connect(self.score_changed)
        self.journal.itemSelectionChanged.connect(self.update_actions)
        self.journal.model().layoutChanged.connect(self.filter_students)
        self.journal.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.work_columns = TableColumns(
            self.journal,
            "written-grades",
            work_columns_menu,
            keys=lambda: (
                "student",
                *(
                    f"work:{work_id}:{field}"
                    for work_id in self._work_ids
                    for field in ("variant", "score")
                ),
            ),
        )
        self.journal.installEventFilter(self)
        bind_table_clear(self.journal, self.clear_written_selection)
        written_layout.addWidget(self.journal, 1)
        self.tabs.addTab(written, "Контрольные работы")
        calculation = QWidget()
        calculation_layout = QVBoxLayout(calculation)
        self.calculation_scroll = QScrollArea()
        self.calculation_scroll.setWidgetResizable(True)
        calculation_content = QWidget()
        box = QVBoxLayout(calculation_content)
        self.calculation_scroll.setWidget(calculation_content)
        totals_tools = self._toolbar(calculation)
        self.settings_action = self._action(
            totals_tools,
            "Настроить расчёт",
            QStyle.StandardPixmap.SP_FileDialogContentsView,
            self.toggle_settings,
        )
        self.advanced_action = self._action(
            totals_tools,
            "Веса и уровни…",
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            self.show_coefficients,
        )
        self.advanced_action.setVisible(False)
        calculation_layout.addWidget(totals_tools)
        calculation_layout.addWidget(self.calculation_scroll, 1)
        self.applied = QLabel()
        self.applied.setWordWrap(True)
        box.addWidget(self.applied)
        fields = QWidget()
        self.calculation_settings = fields
        form = QFormLayout(fields)
        self.parameters = {}
        self.locks = {}
        for field, title, low, high in (
            ("neutral", "Нейтральная карточка", 0, 10),
            ("spread", "Размах доклада", 0.1, 10),
            ("report_weight", "Вес доклада", 0.1, 20),
            ("final_offset", "Сдвиг итоговой шкалы", -5, 5),
            ("final_scale", "Множитель итоговой шкалы", 0.1, 3),
        ):
            control = QDoubleSpinBox()
            control.setRange(low, high)
            control.setSingleStep(0.1)
            lock = QCheckBox("Закрепить")
            row = QHBoxLayout()
            row.addWidget(control)
            row.addWidget(lock)
            form.addRow(title, row)
            self.parameters[field] = control
            self.locks[field] = lock
            control.valueChanged.connect(self.preview)
        self.target_mean = QDoubleSpinBox()
        self.target_mean.setRange(0, 10)
        self.target_mean.setValue(7)
        self.target_deviation = QDoubleSpinBox()
        self.target_deviation.setRange(0, 5)
        self.target_deviation.setValue(1.5)
        form.addRow("Целевая средняя (отдельно от нейтральной)", self.target_mean)
        form.addRow("Целевой разброс", self.target_deviation)
        box.addWidget(fields)
        fields.hide()
        self.analytic_fields = QFormLayout()
        self.analytic_fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.metric_selector = QComboBox()
        self.metric_selector.currentIndexChanged.connect(self.update_variant_filter)
        self.analytic_fields.addRow("Распределение (шкала 0–10)", self.metric_selector)
        self.slice = QComboBox()
        self.slice.currentIndexChanged.connect(self.preview)
        self.analytic_fields.addRow("Аналитический срез", self.slice)
        self.variant_selector = QComboBox()
        self.variant_selector.currentIndexChanged.connect(self.preview)
        self.analytic_fields.addRow("Вариант контрольной", self.variant_selector)
        self.analytic_fields.setRowVisible(self.variant_selector, False)
        box.addLayout(self.analytic_fields)
        self.calculation_actions = QWidget()
        row = QHBoxLayout(self.calculation_actions)
        row.setContentsMargins(0, 0, 0, 0)
        for title, fn in (
            ("Подобрать в указанных пределах", self.fit),
            ("Применить ко всему курсу", self.apply),
            ("Сохранить вариант", self.save_template),
            ("Загрузить вариант", self.load_template),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda _, method=fn: guarded(self, method))
            row.addWidget(button)
        calculation_layout.addWidget(self.calculation_actions)
        self.calculation_actions.hide()
        self.statistics = QLabel()
        self.statistics.setWordWrap(True)
        box.addWidget(self.statistics)
        self.histogram = HistogramWidget()
        self.histogram.setMinimumHeight(150)
        box.addWidget(self.histogram)
        self.variant_notice = plain_label(wrap=True)
        self.variant_notice.hide()
        box.addWidget(self.variant_notice)
        self.variant_stats = table(
            ("Контрольная", "Вариант", "Оценено", "Среднее /10", "σ /10", "Балансировка"),
            name="variant-statistics",
            keys=("work", "variant", "count", "mean", "deviation", "fit"),
            sortable=True,
        )
        self.variant_stats.setMinimumHeight(140)
        self.variant_stats.hide()
        box.addWidget(self.variant_stats, 1)
        self.changes = QTableWidget()
        self.changes.verticalHeader().hide()
        self.changes.setAlternatingRowColors(True)
        self.changes.setMinimumHeight(130)
        self.changes.model().layoutChanged.connect(self.filter_students)
        self.changes.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.change_columns = TableColumns(
            self.changes, "grade-preview", keys=("student", "current", "preview", "readiness")
        )
        box.addWidget(self.changes, 1)
        self.tabs.addTab(calculation, "Итоги")
        advanced = QDialog(self)
        advanced.setWindowTitle("Веса критериев и работ")
        advanced.resize(820, 650)
        self.coefficient_dialog = advanced
        advanced_box = QVBoxLayout(advanced)
        advanced_box.addWidget(
            QLabel(
                "Границы и закрепление используются подбором; уровни и исходные баллы сохраняются."
            )
        )
        self.coefficients = QTableWidget()
        self.coefficients.setColumnCount(5)
        self.coefficients.setHorizontalHeaderLabels(
            ["Параметр", "Значение", "Минимум", "Максимум", "Закрепить"]
        )
        advanced_box.addWidget(self.coefficients)
        self.advanced_controls = {}
        self.coefficients.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(advanced.hide)
        advanced_box.addWidget(close)
        self.update_availability()
        self.update_actions()

    def _toolbar(self, parent):
        return office_toolbar(parent)

    def _action(self, toolbar, text, icon, callback):
        return tool_action(toolbar, text, icon, lambda: guarded(self, callback))

    def update_actions(self):
        course = self.course_id is not None
        self.enable_reports_action.setVisible(course and not self._reports_enabled)
        self.enable_reports_action.setEnabled(course)
        self.open_report_action.setEnabled(
            self._reports_enabled and selected_key(self.report_table) is not None
        )
        self.add_work_action.setEnabled(course)
        current = self.journal.currentItem()
        self.paste_action.setEnabled(
            course
            and bool(self._work_ids)
            and current is not None
            and current.isSelected()
            and current.column() >= 1
            and not self.journal.isRowHidden(current.row())
            and not self.journal.isColumnHidden(current.column())
        )

    def update_availability(self):
        has_work = bool(self._work_ids)
        has_grades = self._reports_enabled or has_work
        availability = (self._reports_enabled, has_work, has_grades)
        for index, available in enumerate(availability):
            self.tabs.setTabVisible(index, available)
            self.tabs.setTabEnabled(index, available)
        active = self.tabs.currentIndex()
        if has_grades and (active < 0 or not availability[active]):
            self.tabs.setCurrentIndex(availability.index(True))
        self.report_table.setVisible(self._reports_enabled)
        self.journal.setVisible(has_work)
        self.tabs.setVisible(has_grades)
        self.empty.setVisible(not has_grades)
        self.search.setVisible(has_grades)
        self.setup_tools.setVisible(self.course_id is not None)
        self.export_action.setVisible(has_grades)

    def open_members(self):
        callback = getattr(self.window(), "show_course_members", None)
        if callable(callback):
            callback()

    def enable_reports(self):
        if self.course_id is None:
            return
        course = self.service.courses.get_course(self.course_id)
        self.service.courses.save_course(
            course.discipline_id,
            course.academic_year_id,
            course.semester,
            course.study_course,
            course.kind,
            course.duration,
            course.name,
            reports_enabled=True,
            course_id=course.id,
        )
        self.refresh()
        self.tabs.setCurrentIndex(0)

    def toggle_settings(self):
        expanded = self.calculation_settings.isHidden()
        self.calculation_settings.setVisible(expanded)
        self.calculation_actions.setVisible(expanded)
        self.advanced_action.setVisible(expanded)
        self.settings_action.setText(
            "Скрыть настройки" if self.calculation_settings.isVisible() else "Настроить расчёт"
        )

    def show_coefficients(self):
        self.coefficient_dialog.show()
        self.coefficient_dialog.raise_()

    def eventFilter(self, watched, event):
        if watched is self.journal and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Paste):
                if self.paste_action.isEnabled():
                    guarded(self, self.paste_scores)
                return True
        return super().eventFilter(watched, event)

    def set_context(self, course_id, lesson_id=None, *, refresh=False):
        same_course = self.course_id == course_id
        self.course_id, self.lesson_id = course_id, lesson_id
        if same_course and course_id is not None and not refresh:
            return
        if not same_course:
            self.report_table.clearSelection()
            self.report_table.setCurrentCell(-1, -1)
            self.journal.clearSelection()
            self.journal.setCurrentCell(-1, -1)
        self.refresh()

    def clear_report_filters(self):
        self.search.clear()
        for control in (self.report_group, self.report_status, self.report_lesson):
            control.setCurrentIndex(0)

    def filter_students(self, *_):
        if self._loading:
            return
        query = self.search.text().strip()
        found = (
            {student.student_id for student in self._student_search.find(query)} if query else None
        )
        report_matches = {
            row["student_id"]
            for row in filter_report_rows(
                self._report_rows,
                group_id=self.report_group.currentData(),
                status={None: "", True: "graded", False: "pending"}[
                    self.report_status.currentData()
                ],
                lesson_id=self.report_lesson.currentData(),
            )
        }
        shown = 0
        for listing in (self.report_table, self.journal, self.changes):
            for row in range(listing.rowCount()):
                student = listing.item(row, 0)
                hidden = student is None or (
                    found is not None and student.data(Qt.ItemDataRole.UserRole) not in found
                )
                if listing is self.report_table and student is not None:
                    hidden |= student.data(Qt.ItemDataRole.UserRole) not in report_matches
                    shown += not hidden
                listing.setRowHidden(row, hidden)
                if hidden:
                    for column in range(listing.columnCount()):
                        item = listing.item(row, column)
                        if item is not None:
                            item.setSelected(False)
        self.report_count.setText(f"Показано {shown} из {self.report_table.rowCount()}")
        self.update_actions()

    def update_report_options(self, control, empty, values):
        previous = control.currentData()
        control.blockSignals(True)
        control.clear()
        control.addItem(empty, None)
        for text, value in values:
            control.addItem(text, value)
        control.setCurrentIndex(max(0, control.findData(previous)))
        control.blockSignals(False)

    def update_variant_filter(self, *_):
        metric = self.metric_selector.currentData()
        work_id = metric if isinstance(metric, int) else None
        previous = self.variant_selector.currentData() if work_id == self._variant_metric else None
        self._variant_metric = work_id
        self.analytic_fields.setRowVisible(self.variant_selector, work_id is not None)
        variants = sorted(
            {variant for (_, key), variant in self._written_variants.items() if key == work_id},
            key=str.casefold,
        )
        has_legacy = any(
            key[1] == work_id and key not in self._written_variants for key in self._written_scores
        )
        control = self.variant_selector
        control.blockSignals(True)
        control.clear()
        control.addItem("Все варианты", None)
        for variant in variants:
            control.addItem(variant, variant)
        if has_legacy:
            control.addItem("Вариант не указан", "")
        control.setCurrentIndex(max(0, control.findData(previous)))
        control.blockSignals(False)
        self.preview()

    def refresh(self):
        context = (
            (self.course_id, self.workflow.parameters(self.course_id).version)
            if self.course_id is not None
            else None
        )
        draft = (
            self.candidate() if context is not None and self._loaded_context == context else None
        )
        self._loading = True
        try:
            self._refresh()
            if draft is not None:
                self._set_candidate(draft)
            self._loaded_context = context
        finally:
            self._loading = False
        self.preview()
        self.filter_students()

    def _refresh(self):
        self.setEnabled(self.course_id is not None)
        self._report_rows = []
        if self.course_id is None:
            self.notice.setText(
                "Выберите курс, чтобы включить доклады или добавить контрольную работу."
            )
            self._work_ids = []
            self._reports_enabled = False
            self._written_scores = {}
            self._written_variants = {}
            self._student_search = StudentSearch(())
            self.report_table.setRowCount(0)
            self.journal.setRowCount(0)
            self.changes.setRowCount(0)
            self.members_button.hide()
            self.update_availability()
            self.update_actions()
            return
        data = self.workflow.view_data(self.course_id)
        students, works = data.students, data.works
        self._student_search = StudentSearch(students)
        self._work_ids = [w.id for w in works]
        self._reports_enabled = data.reports_enabled
        self._written_scores, self._written_variants = data.scores, data.variants
        selected_id = selected_key(self.report_table)
        selected_score = self.journal.currentItem()
        if selected_score is not None and not selected_score.isSelected():
            selected_score = None
        score_student = selected_score.data(Qt.ItemDataRole.UserRole) if selected_score else None
        score_column = self.journal.currentColumn()
        for listing in (self.report_table, self.journal):
            listing.clearSelection()
            listing.setCurrentCell(-1, -1)
        self.report_table.setSortingEnabled(False)
        self.report_table.setColumnCount(5)
        self.report_table.setHorizontalHeaderLabels(
            ["Студент", "Группа", "Тема доклада", "Занятие сдачи", "Оценка /10"]
        )
        self.report_table.setRowCount(len(students))
        lessons = {lesson.id: lesson for lesson in data.lessons}
        groups = {
            s.group_id: (s.group_number, s.specialty) for s in students if s.group_id is not None
        }
        self.update_report_options(
            self.report_group,
            "Все группы",
            [
                (" · ".join(filter(None, (str(number), specialty))), key)
                for key, (number, specialty) in sorted(
                    groups.items(), key=lambda entry: (entry[1][0] or 0, entry[1][1])
                )
            ],
        )
        assessed_lessons = {card.lesson_id for card in data.reports.values()}
        self.report_lesson.replace_choices(
            (
                lesson
                for lesson in data.lessons
                if not lesson.deleted or lesson.id in assessed_lessons
            ),
            self.report_lesson.currentData(),
        )
        for row, student in enumerate(students):
            card = data.reports.get(student.student_id)
            grade = data.results[student.student_id].report
            lesson = lessons.get(card.lesson_id) if card else None
            report = {
                "student_id": student.student_id,
                "full_name": student.full_name,
                "group_id": student.group_id,
                "group_number": student.group_number,
                "topic": card.topic if card else "",
                "lesson_id": card.lesson_id if card else None,
                "lesson_starts_at": lesson.starts_at if lesson else None,
                "grade": grade,
            }
            self._report_rows.append(report)
            values = (
                (student.full_name, "full_name"),
                (student.group_number, "group"),
                (card.topic if card else "Не оценён", "topic"),
                (lesson.label if lesson else "", "lesson"),
                (f"{grade:.2f}" if grade is not None else None, "grade"),
            )
            for column, (value, field) in enumerate(values):
                order = report_sort_value(report, field)
                item = grade_cell(value, student.student_id, (order is not None, order))
                if column == 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.report_table.setItem(row, column, item)
            if student.student_id == selected_id:
                self.report_table.selectRow(row)
        self.report_table.setSortingEnabled(True)
        self.report_columns.restore_visibility()
        self.journal.setSortingEnabled(False)
        self.journal.setColumnCount(1 + 2 * len(works))
        self.journal.setHorizontalHeaderLabels(
            ["Студент"]
            + [
                heading
                for work in works
                for heading in (
                    f"{work.title}\nВариант",
                    f"{work.title}\nБаллы /{work.maximum:g}",
                )
            ]
        )
        self.journal.setRowCount(len(students))
        for row, student in enumerate(students):
            name = grade_cell(student.full_name, student.student_id, student.full_name)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.journal.setItem(row, 0, name)
            for index, work in enumerate(works):
                key = (student.student_id, work.id)
                score = self._written_scores.get(key)
                variant = self.written_variant(key)
                variant_item = grade_cell(variant, student.student_id, variant or "")
                variant_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if variant is None and score is not None:
                    variant_item.setToolTip("Сохранённый балл. Укажите вариант, если он известен.")
                    variant_item.setBackground(QColor("#fff0dc"))
                elif variant is not None and score is None:
                    variant_item.setToolTip("Вариант сохранится вместе с баллом.")
                self.journal.setItem(row, 2 * index + 1, variant_item)
                score_item = grade_cell(score, student.student_id, (score is not None, score))
                score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.journal.setItem(row, 2 * index + 2, score_item)
            if student.student_id == score_student:
                self.journal.setCurrentCell(row, max(0, min(score_column, 2 * len(works))))
        self.journal.setSortingEnabled(True)
        self.work_columns.restore_visibility()
        missing_variants = len(self._written_scores.keys() - self._written_variants.keys())
        legacy_notice = (
            f" Сохранено баллов без варианта: {missing_variants}." if missing_variants else ""
        )
        self.work_notice.setText(
            "Укажите вариант для каждого балла. Для вставки пар из Excel начните с колонки «Вариант», "
            "для вставки только баллов — с колонки «Баллы». "
            "Delete или Backspace очищает выбранные результаты. 0 — выставленный балл."
            + legacy_notice
        )
        parameters = data.parameters
        for field, control in self.parameters.items():
            control.setValue(getattr(parameters, field))
        self.advanced_controls = {}
        definitions = [
            (f"criterion:{i}", c.title, parameters.criterion_weights[i], 0.1, 20.0)
            for i, c in enumerate(CRITERIA)
        ]
        definitions += [
            (f"three:{i}", f"Вклад трёх уровней: {i + 1}", value, -1.0, 1.0)
            for i, value in enumerate(parameters.three_values)
            if i != 1
        ]
        definitions += [
            (f"five:{i}", f"Вклад пяти уровней: {i + 1}", value, -1.0, 1.0)
            for i, value in enumerate(parameters.five_values)
            if i != 2
        ]
        definitions += [
            (
                f"work:{w.id}",
                "Вес: " + w.title,
                dict(parameters.work_weights).get(w.id, w.weight),
                0.1,
                20.0,
            )
            for w in works
        ]
        self.coefficients.setRowCount(len(definitions))
        for row, (key, title, value, low, high) in enumerate(definitions):
            label = QTableWidgetItem(title)
            label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.coefficients.setItem(row, 0, label)
            controls = []
            for column, initial in enumerate((value, low, high), 1):
                control = QDoubleSpinBox()
                control.setRange(low, high)
                control.setSingleStep(0.1)
                control.setValue(initial)
                control.valueChanged.connect(self.preview)
                self.coefficients.setCellWidget(row, column, control)
                controls.append(control)
            lock = QCheckBox()
            self.coefficients.setCellWidget(row, 4, lock)
            self.advanced_controls[key] = (*controls, lock)
        self.applied.setText(
            f"Применённый вариант v{parameters.version}: нейтральная {parameters.neutral:g}; "
            f"размах {parameters.spread:g}; вес доклада {parameters.report_weight:g}; "
            f"перевод ×{parameters.final_scale:g} + {parameters.final_offset:g}"
        )
        metrics = [("Доклад", "report")] if self._reports_enabled else []
        self.update_report_options(
            self.metric_selector,
            "Итог курса",
            [*metrics, *((f"{work.title} /{work.maximum:g} → /10", work.id) for work in works)],
        )
        self.update_variant_filter()
        scopes = [
            (f"Группа {number}", ("group_id", key))
            for key, (number, _) in sorted(
                groups.items(), key=lambda entry: (entry[1][0] or 0, entry[1][1])
            )
        ]
        scopes.extend(
            ("Специальность " + specialty, ("specialty", specialty))
            for specialty in sorted({s.specialty for s in students if s.specialty})
        )
        self.update_report_options(self.slice, "Весь курс", scopes)
        self.notice.setText(
            f"Студентов: {len(students)} · Контрольных: {len(works)}"
            if students
            else "Состав курса пуст. Добавьте учебные группы или импортируйте список студентов."
        )
        if not (self._reports_enabled or works):
            self.notice.setText("В этом курсе пока нет докладов и контрольных работ.")
        self.members_button.setVisible(not students)
        self.update_availability()
        self.update_actions()

    def calculation_draft(self):
        return CalculationDraft(
            **{field: control.value() for field, control in self.parameters.items()},
            coefficients=tuple(
                (key, control.value()) for key, (control, _, _, _) in self.advanced_controls.items()
            ),
        )

    def candidate(self):
        if self.course_id is None:
            raise AttendanceError("Выберите курс для расчёта.")
        return self.previews.candidate(self.course_id, self.calculation_draft())

    def preview_request(self):
        if self.course_id is None:
            raise AttendanceError("Выберите курс для расчёта.")
        self._preview_revision += 1
        selection = self.slice.currentData()
        return GradingPreviewRequest(
            course_id=self.course_id,
            draft=self.calculation_draft(),
            revision=self._preview_revision,
            metric=self.metric_selector.currentData(),
            variant=self.variant_selector.currentData(),
            **({selection[0]: selection[1]} if selection else {}),
        )

    def preview(self, *_):
        if self._loading or self.course_id is None or not (self._reports_enabled or self._work_ids):
            return
        try:
            projection = self.previews.preview(self.preview_request())
        except AttendanceError as exc:
            self.statistics.setText(str(exc))
            return
        stats, current = projection.statistics, projection.baseline_statistics

        def text(value):
            return "—" if value is None else f"{value:.2f}"

        self.statistics.setText(
            f"Применено: N={current.n}, среднее {text(current.mean)}, σ={text(current.deviation)}.\n"
            f"Предпросмотр: N={stats.n}, неполных {stats.incomplete}, среднее {text(stats.mean)}, σ={text(stats.deviation)}.\n"
            + stats.normality
        )
        self.histogram.set_distributions(current.counts, stats.counts)
        self.show_variant_statistics(projection)
        self.changes.setSortingEnabled(False)
        self.changes.setColumnCount(4)
        self.changes.setHorizontalHeaderLabels(
            ["Студент", "Итог /10", "Предпросмотр /10", "Готовность"]
        )
        self.changes.setRowCount(len(projection.changes))
        self.changes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, change in enumerate(projection.changes):
            for j, value in enumerate(
                (
                    change.full_name,
                    change.baseline.mark,
                    change.candidate.mark,
                    change.readiness,
                )
            ):
                item = grade_cell(
                    value, change.student_id, (value is not None, value) if j in (1, 2) else value
                )
                self.changes.setItem(i, j, item)
        self.changes.setSortingEnabled(True)
        self.change_columns.restore_visibility()
        self.filter_students()

    def show_variant_statistics(self, projection):
        rows = projection.variant_statistics
        self.variant_stats.setVisible(bool(rows))
        self.variant_notice.setVisible(bool(self._work_ids))
        fit = projection.fit_statistics
        if fit.balanced:
            count = len(fit.work_ids)
            notice = (
                f"При подборе варианты имеют равный вес внутри {count} контрольных. "
                f"Полных результатов в курсе: {fit.n}."
            )
        else:
            notice = (
                f"Полных результатов в курсе: {fit.n}. "
                f"Для равного веса вариантов нужно не менее {fit.minimum_variant_size} "
                "полных результатов по каждому из нескольких вариантов одной контрольной."
            )
        if fit.mean is not None and fit.deviation is not None and fit.balanced:
            notice += (
                f" Взвешенные среднее и σ в предпросмотре: {fit.mean:.2f} и {fit.deviation:.2f}."
            )
        self.variant_notice.setText(notice)

        def numeric(value):
            return TableValue("—" if value is None else f"{value:.2f}", (value is not None, value))

        fill_table(
            self.variant_stats,
            (
                (
                    row.work_title,
                    row.variant or "Не указан",
                    row.n,
                    numeric(row.mean),
                    numeric(row.deviation),
                    TableValue(f"Да · {row.fit_n}" if row.fit_eligible else "—", row.fit_n),
                )
                for row in rows
            ),
            ((row.work_id, row.variant) for row in rows),
        )

    @staticmethod
    def parse_score(value):
        if not value.strip():
            return None
        try:
            return float(value.replace(",", "."))
        except ValueError:
            raise AttendanceError("Введите число или оставьте ячейку пустой.") from None

    def ask_variant(self, message):
        value, accepted = QInputDialog.getText(self, "Вариант контрольной", message)
        if not accepted:
            return None
        if not value.strip():
            raise AttendanceError("Укажите вариант контрольной.")
        return value.strip()

    def written_variant(self, key):
        return self._written_variants.get(key, self._draft_variants.get(key))

    def save_written_changes(self, changes):
        if changes:
            self.workflow.save_scores(self.course_id, changes)
            for key in changes:
                self._draft_variants.pop(key, None)

    def clear_written_selection(self):
        changes = {
            (item.data(Qt.ItemDataRole.UserRole), self._work_ids[(column - 1) // 2]): None
            for item in self.journal.selectedItems()
            if 0 < (column := item.column()) < self.journal.columnCount()
            and not self.journal.isColumnHidden(column)
            and not self.journal.isRowHidden(item.row())
        }
        if changes:
            self.save_written_changes(changes)
            self.refresh()

    def score_changed(self, item):
        column = item.column()
        if self._loading or not 1 <= column < 1 + 2 * len(self._work_ids):
            return
        key = (item.data(Qt.ItemDataRole.UserRole), self._work_ids[(column - 1) // 2])

        def save():
            if column % 2:
                variant = item.text().strip()
                current = self._written_scores.get(key)
                if current is None:
                    if variant:
                        self._draft_variants[key] = variant
                    else:
                        self._draft_variants.pop(key, None)
                    return
                if not variant:
                    raise AttendanceError("У сохранённого балла должен быть вариант.")
                self.save_written_changes({key: WrittenScore(current, variant)})
                return
            score = self.parse_score(item.text())
            if score is None:
                self.save_written_changes({key: None})
                return
            variant = self.written_variant(key) or self.ask_variant("Вариант для этого балла")
            if variant is not None:
                self.save_written_changes({key: WrittenScore(score, variant)})

        guarded(self, save)
        self.refresh()

    def paste_scores(self):
        from PySide6.QtWidgets import QApplication

        self.update_actions()
        if not self.paste_action.isEnabled():
            return
        pair_mode = bool(self.journal.currentColumn() % 2)
        header = self.journal.horizontalHeader()
        rows = [
            row
            for row in range(self.journal.currentRow(), self.journal.rowCount())
            if not self.journal.isRowHidden(row)
        ]
        columns = [
            column
            for index in range(header.visualIndex(self.journal.currentColumn()), header.count())
            if (column := header.logicalIndex(index)) > 0
            and bool(column % 2) == pair_mode
            and not header.isSectionHidden(column)
        ]
        changes: dict[tuple[str, int], WrittenScore | None] = {}
        drafts: dict[tuple[str, int], str] = {}
        unresolved: dict[tuple[str, int], float] = {}
        step = 2 if pair_mode else 1
        for offset, line in enumerate(QApplication.clipboard().text().splitlines()):
            values = line.split("\t")
            if pair_mode and len(values) % 2:
                raise AttendanceError(
                    "Для каждой контрольной вставьте два столбца: вариант и баллы."
                )
            if offset >= len(rows) or len(values) // step > len(columns):
                raise AttendanceError("Вставка выходит за область видимых ячеек контрольных.")
            student = self.journal.item(rows[offset], 0)
            assert student is not None
            student_id = student.data(Qt.ItemDataRole.UserRole)
            for index in range(0, len(values), step):
                column = columns[index // step]
                if pair_mode and self.journal.isColumnHidden(column + 1):
                    raise AttendanceError(
                        "Для вставки пар покажите обе колонки каждой контрольной."
                    )
                key = (student_id, self._work_ids[(column - 1) // 2])
                variant = values[index].strip() if pair_mode else self.written_variant(key)
                score = self.parse_score(values[index + step - 1])
                if score is None:
                    changes[key] = None
                    if pair_mode and variant:
                        drafts[key] = variant
                elif variant:
                    changes[key] = WrittenScore(score, variant)
                elif pair_mode:
                    raise AttendanceError("Для каждого вставляемого балла укажите вариант.")
                else:
                    unresolved[key] = score
        if unresolved:
            variant = self.ask_variant(
                f"Общий вариант для баллов без варианта ({len(unresolved)}):"
            )
            if variant is None:
                return
            changes.update((key, WrittenScore(score, variant)) for key, score in unresolved.items())
        self.save_written_changes(changes)
        self._draft_variants.update(drafts)
        if changes:
            self.refresh()

    def open_report(self):
        if self.report_dialog is not None:
            if self.report_dialog.isMinimized():
                self.report_dialog.showNormal()
            self.report_dialog.raise_()
            self.report_dialog.activateWindow()
            return
        student_id = selected_key(self.report_table)
        if student_id is None:
            raise AttendanceError("Выберите студента в разделе «Доклады».")
        dialog = ReportCardDialog(self.workflow, self.course_id, student_id, self.lesson_id)
        if self.interface_zoom is not None:
            WidgetZoom(dialog, self.interface_zoom)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(self._report_finished)
        self.report_dialog = dialog
        dialog.show()

    def close_report(self):
        if self.report_dialog is not None:
            self.report_dialog.close()

    def _report_finished(self, result):
        self.report_dialog = None
        if result == QDialog.DialogCode.Accepted:
            self.refresh()

    def add_work(self):
        title, ok = QInputDialog.getText(self, "Новая контрольная работа", "Название")
        if not ok:
            return
        maximum, ok = QInputDialog.getDouble(
            self, "Исходная шкала", "Максимум", 100, 0.01, 100000, 2
        )
        if ok:
            self.workflow.add_work(self.course_id, title, maximum)
            self.refresh()
            self.tabs.setCurrentIndex(1)

    def fit(self):
        bounds = tuple(
            FitBound(field, control.minimum(), control.maximum(), self.locks[field].isChecked())
            for field, control in self.parameters.items()
        )
        bounds += tuple(
            FitBound(key, low.value(), high.value(), lock.isChecked())
            for key, (_, low, high, lock) in self.advanced_controls.items()
        )
        found = self.previews.fit(
            GradingFitRequest(
                self.preview_request(),
                bounds,
                self.target_mean.value(),
                self.target_deviation.value(),
            )
        )
        self._set_candidate(found.parameters)

    def _set_candidate(self, parameters):
        for field, control in self.parameters.items():
            control.setValue(getattr(parameters, field))
        for key, (control, _, _, _) in self.advanced_controls.items():
            kind, index = key.split(":")
            values = {
                "criterion": parameters.criterion_weights,
                "three": parameters.three_values,
                "five": parameters.five_values,
                "work": dict(parameters.work_weights),
            }
            control.setValue(
                values[kind].get(int(index), control.value())
                if kind == "work"
                else values[kind][int(index)]
            )

    def apply(self):
        self.workflow.apply_parameters(self.course_id, self.candidate())
        self.refresh()

    def save_template(self):
        name, ok = QInputDialog.getText(self, "Вариант расчёта", "Название")
        if ok:
            self.workflow.save_template(name, self.candidate())

    def load_template(self):
        templates = self.workflow.templates(self.course_id)
        name, ok = QInputDialog.getItem(
            self, "Варианты расчёта", "Выберите", list(templates), 0, False
        )
        if ok:
            self._set_candidate(templates[name])

    def export(self, *, observed_only=False):
        filename, _ = QFileDialog.getSaveFileName(
            self, "Экспорт журнала", "journal.xlsx", "Excel (*.xlsx)"
        )
        if filename:
            headers, rows = self.workflow.export_projection(
                self.course_id, observed_only=observed_only
            )
            if self.files is None:
                raise AttendanceError("Экспорт недоступен: файловый адаптер не подключён.")
            self.files.export_xlsx(Path(filename), headers, rows)
