"""Report cards, original written scores and course-wide calculation preview."""

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter
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
    QLineEdit,
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

from attendance_lab.domain.grading import (
    CRITERIA,
    FitBound,
    ReportCard,
    fit_parameters,
    report_grade,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.presentation.common import guarded, selected_key
from attendance_lab.presentation.report_criteria import ReportCriteriaPanel
from attendance_lab.presentation.report_topic_picker import ReportTopicPicker
from attendance_lab.presentation.toolbars import office_toolbar, tool_action


class ReportCardDialog(QDialog):
    def __init__(self, workflow, course_id, student_id, lesson_id=None, parent=None):
        super().__init__(parent)
        self.workflow = workflow
        self.course_id, self.student_id = course_id, student_id
        student = next(
            (s for s in workflow.students(course_id) if s.student_id == student_id), None
        )
        self.setWindowTitle("Доклад — " + (student.full_name if student else "карточка оценивания"))
        available = self.screen().availableGeometry()
        self.resize(min(920, available.width() - 40), min(820, available.height() - 60))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.topic = ReportTopicPicker(workflow.report_topic_catalog(course_id))
        self.lesson = QComboBox()
        for key, label in workflow.lessons(course_id):
            self.lesson.addItem(label, key)
        self.written = QCheckBox("Письменный реферат представлен")
        card = workflow.report(course_id, student_id)
        if card and self.lesson.findData(card.lesson_id) < 0:
            historical = dict(workflow.lessons(course_id, include_deleted=True))
            if card.lesson_id in historical:
                self.lesson.addItem(historical[card.lesson_id], card.lesson_id)
        if card:
            self.topic.setCurrentText(card.topic)
            lesson_id = card.lesson_id
        else:
            self.topic.setCurrentText("")
        self.lesson.setCurrentIndex(self.lesson.findData(lesson_id))
        self.lesson.setEnabled(card is None)
        change = QPushButton("Изменить занятие сдачи…")
        change.setVisible(card is not None)
        change.clicked.connect(lambda: self.lesson.setEnabled(True))
        form.addRow("Тема реферата", self.topic)
        form.addRow("Занятие сдачи", self.lesson)
        form.addRow("", change)
        form.addRow("", self.written)
        layout.addLayout(form)
        self.criteria = ReportCriteriaPanel(
            card.levels if card else None, card.written if card else False
        )
        self.sliders = self.criteria.controls
        self.criteria.levelsChanged.connect(self.update_preview)
        layout.addWidget(self.criteria, 1)
        self.preview = QLabel()
        layout.addWidget(self.preview)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
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
        self.update_preview()

    def update_preview(self, *_):
        self.criteria.set_written(self.written.isChecked())
        temporary = ReportCard(
            self.course_id,
            self.student_id,
            self.lesson.currentData() or 0,
            self.topic.currentText() or "Предпросмотр",
            self.written.isChecked(),
            tuple(s.value() for s in self.sliders),
        )
        self.preview.setText(
            f"Предварительная оценка: {report_grade(temporary, self.workflow.parameters(self.course_id)):.2f} / 10"
        )

    def save(self):
        if self.lesson.currentData() is None:
            raise AttendanceError("Выберите конкретное занятие сдачи.")
        self.workflow.save_report(
            ReportCard(
                self.course_id,
                self.student_id,
                self.lesson.currentData(),
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


class GradingPane(QWidget):
    def __init__(self, service, files=None, parent=None):
        super().__init__(parent)
        self.files = files
        self.service = service
        self.workflow = service.grading
        self.course_id = self.lesson_id = None
        self._loading = False
        self._loaded_context = None
        self._student_ids = []
        self._work_ids = []
        layout = QVBoxLayout(self)
        self.notice = QLabel("Выберите курс.")
        self.notice.setWordWrap(True)
        notice_row = QHBoxLayout()
        notice_row.addWidget(self.notice, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск студента · F3")
        self.search.setFixedWidth(230)
        self.search.textChanged.connect(self.filter_students)
        notice_row.addWidget(self.search)
        self.members_button = QPushButton("Добавить студентов…")
        self.members_button.clicked.connect(self.open_members)
        self.members_button.hide()
        notice_row.addWidget(self.members_button)
        layout.addLayout(notice_row)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        reports = QWidget()
        report_layout = QVBoxLayout(reports)
        report_tools = self._toolbar(reports)
        self.enable_reports_action = self._action(
            report_tools,
            "Включить доклады",
            QStyle.StandardPixmap.SP_DialogApplyButton,
            self.enable_reports,
        )
        self.open_report_action = self._action(
            report_tools,
            "Оценить доклад",
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            self.open_report,
        )
        self._action(
            report_tools, "Экспорт XLSX", QStyle.StandardPixmap.SP_DialogSaveButton, self.export
        )
        report_layout.addWidget(report_tools)
        self.report_notice = QLabel()
        self.report_notice.setWordWrap(True)
        self.report_notice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        report_layout.addWidget(self.report_notice)
        self.report_table = QTableWidget()
        self.report_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.report_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.report_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.report_table.itemSelectionChanged.connect(self.update_actions)
        self.report_table.itemDoubleClicked.connect(lambda *_: guarded(self, self.open_report))
        report_layout.addWidget(self.report_table, 1)
        self.report_empty = QWidget()
        report_layout.addWidget(self.report_empty, 1)
        self.tabs.addTab(reports, "Доклады")
        written = QWidget()
        written_layout = QVBoxLayout(written)
        written_tools = self._toolbar(written)
        self.add_work_action = self._action(
            written_tools, "Добавить контрольную", QStyle.StandardPixmap.SP_FileIcon, self.add_work
        )
        self.paste_action = self._action(
            written_tools,
            "Вставить баллы",
            QStyle.StandardPixmap.SP_FileDialogListView,
            self.paste_scores,
        )
        self._action(
            written_tools, "Экспорт XLSX", QStyle.StandardPixmap.SP_DialogSaveButton, self.export
        )
        written_layout.addWidget(written_tools)
        self.work_notice = QLabel()
        self.work_notice.setWordWrap(True)
        self.work_notice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        written_layout.addWidget(self.work_notice)
        self.journal = QTableWidget()
        self.journal.cellChanged.connect(self.score_changed)
        self.journal.itemSelectionChanged.connect(self.update_actions)
        self.journal.installEventFilter(self)
        written_layout.addWidget(self.journal, 1)
        self.work_empty = QWidget()
        written_layout.addWidget(self.work_empty, 1)
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
        self._action(
            totals_tools, "Экспорт XLSX", QStyle.StandardPixmap.SP_DialogSaveButton, self.export
        )
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
        self.metric_selector = QComboBox()
        self.metric_selector.currentIndexChanged.connect(self.preview)
        form.addRow("Распределение (шкала 0–10)", self.metric_selector)
        self.slice = QComboBox()
        self.slice.currentIndexChanged.connect(self.preview)
        form.addRow("Аналитический срез", self.slice)
        box.addWidget(fields)
        fields.hide()
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
        self.changes = QTableWidget()
        self.changes.setMinimumHeight(130)
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
        self.update_actions()

    def _toolbar(self, parent):
        return office_toolbar(parent)

    def _action(self, toolbar, text, icon, callback):
        return tool_action(toolbar, text, icon, lambda: guarded(self, callback))

    def update_actions(self):
        course = self.course_id is not None
        enabled = course and self.workflow.reports_enabled(self.course_id)
        self.enable_reports_action.setVisible(not enabled)
        self.enable_reports_action.setEnabled(course)
        self.open_report_action.setEnabled(enabled and selected_key(self.report_table) is not None)
        self.add_work_action.setEnabled(course)
        self.paste_action.setEnabled(
            course
            and bool(self._work_ids)
            and self.journal.currentItem() is not None
            and self.journal.currentItem().isSelected()
            and self.journal.currentColumn() >= 1
        )

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
        self.report_table.clearSelection()
        self.report_table.setCurrentCell(-1, -1)
        self.journal.clearSelection()
        self.journal.setCurrentCell(-1, -1)
        self.refresh()

    def filter_students(self, *_):
        query = self.search.text().casefold().replace("ё", "е")
        for listing in (self.report_table, self.journal, self.changes):
            for row in range(listing.rowCount()):
                student = listing.item(row, 0)
                hidden = bool(student and query not in student.text().casefold().replace("ё", "е"))
                listing.setRowHidden(row, hidden)
                if hidden:
                    for column in range(listing.columnCount()):
                        item = listing.item(row, column)
                        if item is not None:
                            item.setSelected(False)
        self.update_actions()

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
        if self.course_id is None:
            self.notice.setText(
                "Выберите курс, чтобы включить доклады или добавить контрольную работу."
            )
            self._student_ids = []
            self._work_ids = []
            self.report_table.setRowCount(0)
            self.journal.setRowCount(0)
            self.changes.setRowCount(0)
            self.members_button.hide()
            self.update_actions()
            return
        students = self.workflow.students(self.course_id)
        works = self.workflow.works(self.course_id)
        results = {r.student_id: r for r in self.workflow.results(self.course_id)}
        scores = self.workflow.scores(self.course_id)
        self._student_ids = [s.student_id for s in students]
        self._work_ids = [w.id for w in works]
        selected_id = selected_key(self.report_table)
        selected_score = self.journal.currentItem()
        if selected_score is not None and not selected_score.isSelected():
            selected_score = None
        score_student = selected_score.data(Qt.ItemDataRole.UserRole) if selected_score else None
        score_column = self.journal.currentColumn()
        for listing in (self.report_table, self.journal):
            listing.clearSelection()
            listing.setCurrentCell(-1, -1)
        self.report_table.setColumnCount(5)
        self.report_table.setHorizontalHeaderLabels(
            ["Студент", "Группа", "Тема доклада", "Занятие сдачи", "Оценка /10"]
        )
        self.report_table.setRowCount(len(students))
        lessons = dict(self.workflow.lessons(self.course_id, include_deleted=True))
        for row, student in enumerate(students):
            card = self.workflow.report(self.course_id, student.student_id)
            values = (
                student.full_name,
                student.group_number,
                card.topic if card else "Не оценён",
                lessons.get(card.lesson_id, "") if card else "",
                results[student.student_id].report,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem("" if value is None else str(value))
                item.setData(Qt.ItemDataRole.UserRole, student.student_id)
                item.setToolTip(item.text())
                self.report_table.setItem(row, column, item)
            if student.student_id == selected_id:
                self.report_table.selectRow(row)
        self.report_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.report_table.setColumnWidth(1, 70)
        self.report_table.setColumnWidth(2, 210)
        self.report_table.setColumnWidth(3, 240)
        self.report_table.setColumnWidth(4, 90)
        self.journal.setSortingEnabled(False)
        self.journal.setColumnCount(1 + len(works))
        self.journal.setHorizontalHeaderLabels(
            ["Студент"] + [f"{w.title} /{w.maximum:g}" for w in works]
        )
        self.journal.setRowCount(len(students))
        self.journal.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.journal.setColumnWidth(0, 280)
        for column in range(1, len(works) + 1):
            self.journal.setColumnWidth(column, 190)
        self.journal.horizontalHeader().setStretchLastSection(True)
        for row, student in enumerate(students):
            values = [student.full_name] + [scores.get((student.student_id, w.id)) for w in works]
            for column, value in enumerate(values):
                item = QTableWidgetItem("" if value is None else str(value))
                item.setData(Qt.ItemDataRole.UserRole, student.student_id)
                if column == 0:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.journal.setItem(row, column, item)
            if student.student_id == score_student:
                self.journal.setCurrentCell(row, max(0, min(score_column, len(works))))
        self.journal.setVisible(bool(works))
        self.work_empty.setVisible(not works)
        self.work_notice.setText(
            "Контрольных пока нет. Нажмите «Добавить контрольную», задайте её название и шкалу."
            if not works
            else "Введите баллы в ячейки или вставьте прямоугольник из Excel (Ctrl+V). Пусто — результата нет; 0 — выставленный балл."
        )
        parameters = self.workflow.parameters(self.course_id)
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
        selected_metric = self.metric_selector.currentData()
        self.metric_selector.clear()
        self.metric_selector.addItem("Итог курса", None)
        if self.workflow.reports_enabled(self.course_id):
            self.metric_selector.addItem("Доклад", "report")
        for work in works:
            self.metric_selector.addItem(f"{work.title} /{work.maximum:g} → /10", work.id)
        self.metric_selector.setCurrentIndex(max(0, self.metric_selector.findData(selected_metric)))
        self.slice.clear()
        self.slice.addItem("Весь курс", None)
        for key, title in sorted({(s.group_id, str(s.group_number)) for s in students}):
            self.slice.addItem("Группа " + title, ("group_id", key))
        for specialty in sorted({s.specialty for s in students}):
            self.slice.addItem("Специальность " + specialty, ("specialty", specialty))
        enabled = self.workflow.reports_enabled(self.course_id)
        self.report_table.setVisible(enabled)
        self.report_empty.setVisible(not enabled)
        self.report_notice.setText(
            "Доклады пока не включены. Нажмите «Включить доклады», затем выберите студента и оцените выступление."
            if not enabled
            else (
                "Дважды нажмите на студента, чтобы открыть карточку из 18 критериев."
                if students
                else "В курсе пока нет студентов. Добавьте годовые группы в разделе «Курс»."
            )
        )
        self.notice.setText(
            f"Студентов: {len(students)} · Доклады: {'включены' if enabled else 'не включены'} · Контрольных: {len(works)}"
            if students
            else "Состав курса пуст. Добавьте учебные группы или импортируйте список студентов."
        )
        self.members_button.setVisible(not students)
        self.update_actions()

    def candidate(self):
        parameters = self.workflow.parameters(self.course_id)
        weights = list(parameters.criterion_weights)
        three, five = list(parameters.three_values), list(parameters.five_values)
        works = {}
        for key, (control, _, _, _) in self.advanced_controls.items():
            kind, index = key.split(":")
            if kind == "work":
                works[int(index)] = control.value()
            else:
                {"criterion": weights, "three": three, "five": five}[kind][int(index)] = (
                    control.value()
                )
        return replace(
            parameters,
            criterion_weights=tuple(weights),
            three_values=tuple(three),
            five_values=tuple(five),
            work_weights=tuple(works.items()),
            **{field: control.value() for field, control in self.parameters.items()},
        )

    def preview(self, *_):
        if self._loading or self.course_id is None:
            return
        try:
            params = self.candidate()
        except AttendanceError as exc:
            self.statistics.setText(str(exc))
            return
        selection = self.slice.currentData()
        kwargs = {selection[0]: selection[1]} if selection else {}
        kwargs["metric"] = self.metric_selector.currentData()
        stats = self.workflow.statistics(self.course_id, params, **kwargs)
        current = self.workflow.statistics(self.course_id, **kwargs)

        def text(value):
            return "—" if value is None else f"{value:.2f}"

        self.statistics.setText(
            f"Применено: N={current.n}, среднее {text(current.mean)}, σ={text(current.deviation)}.\n"
            f"Предпросмотр: N={stats.n}, неполных {stats.incomplete}, среднее {text(stats.mean)}, σ={text(stats.deviation)}.\n"
            + stats.normality
        )
        self.histogram.set_distributions(current.counts, stats.counts)
        baseline = {r.student_id: r for r in self.workflow.results(self.course_id)}
        results = self.workflow.results(self.course_id, params)
        students = {s.student_id: s for s in self.workflow.students(self.course_id)}
        self.changes.setColumnCount(4)
        self.changes.setHorizontalHeaderLabels(
            ["Студент", "Итог /10", "Предпросмотр /10", "Готовность"]
        )
        self.changes.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.changes.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.changes.setColumnWidth(1, 110)
        self.changes.setColumnWidth(2, 160)
        self.changes.setRowCount(len(results))
        self.changes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, result in enumerate(results):
            for j, value in enumerate(
                (
                    students[result.student_id].full_name,
                    baseline[result.student_id].mark,
                    result.mark,
                    "Не хватает: " + ", ".join(result.missing)
                    if result.missing
                    else (
                        "Все работы оценены"
                        if result.final is not None
                        else "Добавьте доклады или контрольную"
                    ),
                )
            ):
                item = QTableWidgetItem("" if value is None else str(value))
                item.setToolTip(item.text())
                self.changes.setItem(i, j, item)

    def score_changed(self, row, column):
        if self._loading or not 1 <= column < 1 + len(self._work_ids):
            return

        def save():
            item = self.journal.item(row, column)
            assert item is not None
            score = float(item.text().replace(",", ".")) if item.text().strip() else None
            self.workflow.save_scores(
                self.course_id,
                {(item.data(Qt.ItemDataRole.UserRole), self._work_ids[column - 1]): score},
            )
            self.refresh()

        guarded(self, save)

    def paste_scores(self):
        from PySide6.QtWidgets import QApplication

        self.update_actions()
        if not self.paste_action.isEnabled():
            return
        row, column = self.journal.currentRow(), self.journal.currentColumn()
        changes = {}
        for dr, line in enumerate(QApplication.clipboard().text().splitlines()):
            for dc, value in enumerate(line.split("\t")):
                target_row, target_column = row + dr, column + dc
                if not (
                    0 <= target_row < len(self._student_ids)
                    and 1 <= target_column < 1 + len(self._work_ids)
                ):
                    raise AttendanceError("Вставка выходит за область исходных баллов.")
                changes[(self._student_ids[target_row], self._work_ids[target_column - 1])] = (
                    float(value.replace(",", ".")) if value.strip() else None
                )
        self.workflow.save_scores(self.course_id, changes)
        self.refresh()

    def open_report(self):
        student_id = selected_key(self.report_table)
        if student_id is None:
            raise AttendanceError("Выберите студента в разделе «Доклады».")
        if (
            ReportCardDialog(self.workflow, self.course_id, student_id, self.lesson_id, self).exec()
            == QDialog.DialogCode.Accepted
        ):
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

    def fit(self):
        bounds = tuple(
            FitBound(field, control.minimum(), control.maximum(), self.locks[field].isChecked())
            for field, control in self.parameters.items()
        )
        bounds += tuple(
            FitBound(key, low.value(), high.value(), lock.isChecked())
            for key, (_, low, high, lock) in self.advanced_controls.items()
        )
        found = fit_parameters(
            self.candidate(),
            self.workflow.evaluator(self.course_id),
            bounds,
            self.target_mean.value(),
            self.target_deviation.value(),
        )
        self._set_candidate(found)

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
