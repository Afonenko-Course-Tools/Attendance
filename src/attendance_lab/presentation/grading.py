"""Report cards, original written scores and course-wide calculation preview."""

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
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
from attendance_lab.presentation.common import guarded


class ReportCardDialog(QDialog):
    def __init__(self, workflow, course_id, student_id, lesson_id=None, parent=None):
        super().__init__(parent)
        self.workflow = workflow
        self.course_id, self.student_id = course_id, student_id
        self.setWindowTitle("Доклад — сохранить карточку целиком")
        self.resize(750, 700)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.topic = QComboBox()
        self.topic.setEditable(True)
        self.topic.addItems(workflow.report_topics(course_id))
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
        change.clicked.connect(lambda: self.lesson.setEnabled(True))
        form.addRow("Тема (из списка или своя)", self.topic)
        form.addRow("Занятие сдачи", self.lesson)
        form.addRow("", change)
        form.addRow("", self.written)
        layout.addLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        questions = QFormLayout(body)
        self.sliders = []
        self.level_labels = []
        for index, criterion in enumerate(CRITERIA):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, criterion.steps - 1)
            slider.setTickInterval(1)
            slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            slider.setAccessibleName(criterion.title)
            slider.setValue(card.levels[index] if card else criterion.steps // 2)
            label = QLabel()
            line = QHBoxLayout()
            line.addWidget(slider, 1)
            line.addWidget(label)
            questions.addRow(criterion.title, line)
            slider.installEventFilter(self)
            self.sliders.append(slider)
            self.level_labels.append(label)
            slider.valueChanged.connect(self.update_preview)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
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

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and watched in self.sliders:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                direction = -1 if event.key() == Qt.Key.Key_Up else 1
                index = self.sliders.index(watched) + direction
                while 0 <= index < len(self.sliders):
                    if self.sliders[index].isEnabled():
                        self.sliders[index].setFocus()
                        break
                    index += direction
                return True
        return super().eventFilter(watched, event)

    def update_preview(self, *_):
        for criterion, slider, label in zip(CRITERIA, self.sliders, self.level_labels, strict=True):
            slider.setEnabled(not criterion.written or self.written.isChecked())
            labels = (
                ("Хуже", "Нейтрально", "Лучше")
                if criterion.steps == 3
                else ("Значительно хуже", "Хуже", "Нейтрально", "Лучше", "Значительно лучше")
            )
            label.setText(labels[slider.value()])
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
        self.workflow = service.grading
        self.course_id = self.lesson_id = None
        self._loading = False
        self._student_ids = []
        self._work_ids = []
        layout = QVBoxLayout(self)
        self.notice = QLabel("Выберите курс.")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        for title, method in (
            ("Открыть доклад", self.open_report),
            ("Добавить работу", self.add_work),
            ("Вставить баллы", self.paste_scores),
            ("Экспорт XLSX", self.export),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda _, fn=method: guarded(self, fn))
            actions.addWidget(button)
        layout.addLayout(actions)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.journal = QTableWidget()
        self.journal.cellChanged.connect(self.score_changed)
        self.tabs.addTab(self.journal, "Работы")
        calculation = QWidget()
        calculation_layout = QVBoxLayout(calculation)
        self.calculation_scroll = QScrollArea()
        self.calculation_scroll.setWidgetResizable(True)
        calculation_content = QWidget()
        box = QVBoxLayout(calculation_content)
        self.calculation_scroll.setWidget(calculation_content)
        calculation_layout.addWidget(self.calculation_scroll, 1)
        self.applied = QLabel()
        self.applied.setWordWrap(True)
        box.addWidget(self.applied)
        fields = QWidget()
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
        row = QHBoxLayout()
        for title, fn in (
            ("Подобрать в указанных пределах", self.fit),
            ("Применить ко всему курсу", self.apply),
            ("Сохранить вариант", self.save_template),
            ("Загрузить вариант", self.load_template),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda _, method=fn: guarded(self, method))
            row.addWidget(button)
        calculation_layout.addLayout(row)
        self.statistics = QLabel()
        self.statistics.setWordWrap(True)
        box.addWidget(self.statistics)
        self.histogram = HistogramWidget()
        self.histogram.setMinimumHeight(150)
        box.addWidget(self.histogram)
        self.changes = QTableWidget()
        self.changes.setMinimumHeight(130)
        box.addWidget(self.changes, 1)
        self.tabs.addTab(calculation, "Расчёт и статистика")
        advanced = QWidget()
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
        self.tabs.addTab(advanced, "Веса и уровни")

    def set_context(self, course_id, lesson_id=None):
        self.course_id, self.lesson_id = course_id, lesson_id
        self.refresh()

    def refresh(self):
        self._loading = True
        try:
            self._refresh()
        finally:
            self._loading = False
        self.preview()

    def _refresh(self):
        self.setEnabled(self.course_id is not None)
        if self.course_id is None:
            return
        students = self.workflow.students(self.course_id)
        works = self.workflow.works(self.course_id)
        results = {r.student_id: r for r in self.workflow.results(self.course_id)}
        scores = self.workflow.scores(self.course_id)
        self._student_ids = [s.student_id for s in students]
        self._work_ids = [w.id for w in works]
        self.journal.setSortingEnabled(False)
        self.journal.setColumnCount(3 + len(works))
        self.journal.setHorizontalHeaderLabels(
            ["ФИО", "Доклад /10"] + [f"{w.title} /{w.maximum:g}" for w in works] + ["Итог /10"]
        )
        self.journal.setRowCount(len(students))
        for i, student in enumerate(students):
            result = results[student.student_id]
            values = [student.full_name, result.report]
            values += [scores.get((student.student_id, w.id)) for w in works]
            values += [result.mark]
            for j, value in enumerate(values):
                item = QTableWidgetItem("" if value is None else str(value))
                item.setData(Qt.ItemDataRole.UserRole, student.student_id)
                if j < 2 or j == len(values) - 1:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.journal.setItem(i, j, item)
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
        self.notice.setText(
            "Пусто — результата нет; 0 — выставленный результат. Все работы обязательны. "
            + ("Доклад включён." if enabled else "Доклад не включён.")
        )

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
        self.changes.setColumnCount(3)
        self.changes.setHorizontalHeaderLabels(["ФИО", "Применено", "Предпросмотр"])
        self.changes.setRowCount(len(results))
        self.changes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, result in enumerate(results):
            for j, value in enumerate(
                (
                    students[result.student_id].full_name,
                    baseline[result.student_id].mark,
                    result.mark,
                )
            ):
                self.changes.setItem(i, j, QTableWidgetItem("" if value is None else str(value)))

    def score_changed(self, row, column):
        if self._loading or not 2 <= column < 2 + len(self._work_ids):
            return

        def save():
            item = self.journal.item(row, column)
            assert item is not None
            score = float(item.text().replace(",", ".")) if item.text().strip() else None
            self.workflow.save_scores(
                self.course_id,
                {(item.data(Qt.ItemDataRole.UserRole), self._work_ids[column - 2]): score},
            )
            self.refresh()

        guarded(self, save)

    def paste_scores(self):
        from PySide6.QtWidgets import QApplication

        row, column = self.journal.currentRow(), self.journal.currentColumn()
        changes = {}
        for dr, line in enumerate(QApplication.clipboard().text().splitlines()):
            for dc, value in enumerate(line.split("\t")):
                target_row, target_column = row + dr, column + dc
                if not (
                    0 <= target_row < len(self._student_ids)
                    and 2 <= target_column < 2 + len(self._work_ids)
                ):
                    raise AttendanceError("Вставка выходит за область исходных баллов.")
                changes[(self._student_ids[target_row], self._work_ids[target_column - 2])] = (
                    float(value.replace(",", ".")) if value.strip() else None
                )
        self.workflow.save_scores(self.course_id, changes)
        self.refresh()

    def open_report(self):
        row = self.journal.currentRow()
        if row < 0:
            raise AttendanceError("Выберите студента в журнале.")
        if (
            ReportCardDialog(
                self.workflow, self.course_id, self._student_ids[row], self.lesson_id, self
            ).exec()
            == QDialog.DialogCode.Accepted
        ):
            self.refresh()

    def add_work(self):
        title, ok = QInputDialog.getText(self, "Новая обязательная работа", "Название")
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

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, "Экспорт журнала", "journal.xlsx", "Excel (*.xlsx)"
        )
        if filename:
            headers, rows = self.workflow.export_projection(self.course_id)
            if self.files is None:
                raise AttendanceError("Экспорт недоступен: файловый адаптер не подключён.")
            self.files.export_xlsx(Path(filename), headers, rows)
