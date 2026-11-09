"""Course centred programme, membership and schedule commands."""

from typing import Any, cast

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.redesign import SLOTS
from attendance_lab.presentation.common import (
    actions,
    fill_table,
    guarded,
    plain_label,
    selected_key,
    table,
)
from attendance_lab.presentation.grading import GradingPane
from attendance_lab.presentation.lesson_lifecycle import (
    delete_lesson_dialog,
    restore_lessons_dialog,
)
from attendance_lab.presentation.preparation import FormDialog, checked, choices, combo, spin


class CourseWorkspace(QWidget):
    def __init__(self, service, changed, lesson_workspace, preparation, parent=None, *, files=None):
        super().__init__(parent)
        self.service, self.api, self.changed = service, service.courses, changed
        self.lesson_workspace = lesson_workspace
        self.journal_pane = None
        outer = QVBoxLayout(self)
        self.courses = QComboBox()
        outer.addWidget(self.courses)
        outer.addLayout(
            actions(
                ("Новый курс…", self.new_course),
                ("Копировать в семестр…", self.copy_course),
                ("Возможности и настройки…", self.settings_dialog),
                ("Добавить письменную работу…", self.add_written),
            )
        )
        self.summary = plain_label(wrap=True)
        outer.addWidget(self.summary)
        self.tabs = QTabWidget()
        programme = QWidget()
        layout = QVBoxLayout(programme)
        layout.addLayout(actions(("Добавить / изменить тему…", self.topic_dialog)))
        self.topics = table(["№", "Тема"])
        layout.addWidget(self.topics)
        members = QWidget()
        layout = QVBoxLayout(members)
        layout.addLayout(
            actions(
                ("Добавить годовые группы…", self.groups_dialog),
                ("Создать подгруппу…", lambda: self.groups_dialog(True)),
            )
        )
        self.members = table(["Состав", "Группы", "Студентов"])
        layout.addWidget(self.members)
        schedule = QWidget()
        layout = QVBoxLayout(schedule)
        layout.addLayout(
            actions(
                ("Запланировать…", self.schedule_dialog),
                ("Перенести выбранное…", self.move_dialog),
                ("Регулярность с даты…", self.recurrence_dialog),
                ("Просмотр занятия", self.view_lesson),
            )
        )
        layout.addLayout(
            actions(
                ("Удалить выбранное…", self.delete_selected),
                (
                    "Удалённые занятия…",
                    lambda: restore_lessons_dialog(self, self.service, self.changed),
                ),
            )
        )
        self.lessons = table(["Дата", "Пары", "Тема", "Состав", "Аудитория"])
        layout.addWidget(self.lessons)
        self.lessons.itemDoubleClicked.connect(lambda *_: self.view_lesson())
        self.tabs.addTab(programme, "Программа")
        self.tabs.addTab(members, "Состав")
        self.tabs.addTab(schedule, "Расписание")
        self.journal = table(["ФИО"])
        self.journal.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.journal.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        jp = QWidget()
        self.journal_pane = jp
        jl = QVBoxLayout(jp)
        jl.addLayout(
            actions(
                ("Отметить выделенные темы…", self.mark_rectangle),
                ("Экспорт XLSX…", lambda: guarded(self, self.export_journal)),
            )
        )
        filters = QHBoxLayout()
        self.journal_search = QLineEdit()
        self.journal_search.setPlaceholderText("ФИО, группа, подгруппа, специальность")
        self.journal_search.textChanged.connect(self.filter_journal)
        filters.addWidget(self.journal_search)
        context = QCheckBox("Учебный контекст")
        context.toggled.connect(
            lambda visible: [self.journal.setColumnHidden(c, not visible) for c in range(1, 5)]
        )
        filters.addWidget(context)
        self.journal_sort = combo(
            [("По ФИО", 0), ("По группе", 1), ("По подгруппе", 2), ("По специальности", 3)]
        )
        self.journal_sort.currentIndexChanged.connect(
            lambda: self.journal.sortItems(
                self.journal_sort.currentData(), Qt.SortOrder.AscendingOrder
            )
        )
        filters.addWidget(self.journal_sort)
        jl.addLayout(filters)
        jl.addWidget(self.journal)
        self.tabs.addTab(jp, "Посещения")
        self.grading = GradingPane(service, files=files, parent=self)

        self.lessons.itemSelectionChanged.connect(
            lambda: self.grading.set_context(self.current_course_id, self.selected_lesson())
        )
        self.tabs.addTab(preparation, "Учебный список и каталоги")
        outer.addWidget(self.tabs)
        self.courses.currentIndexChanged.connect(self.show_course)
        self.lesson_workspace.blocks.currentIndexChanged.connect(self.sync_lesson_context)

    @property
    def current_course_id(self):
        return self.courses.currentData()

    def refresh(self):
        selected = self.current_course_id
        self.courses.blockSignals(True)
        self.courses.clear()
        for c in self.api.list_courses():
            self.courses.addItem(f"{c.name} · {c.study_course} курс · семестр {c.semester}", c.id)
        self.courses.setCurrentIndex(max(0, self.courses.findData(selected)))
        self.courses.blockSignals(False)
        self.show_course()

    def run(self, d, save):
        d.finish()
        if d.exec() == QDialog.DialogCode.Accepted:
            guarded(self, lambda: (save(), self.changed(), self.refresh()))

    def show_course(self, *_):
        key = self.current_course_id
        if key is None:
            self.summary.setText("Создайте семестровый курс, затем добавьте программу и группы.")
            return
        c = self.api.get_course(key)
        self.grading.set_context(key, None)
        self.lesson_workspace.selected_course_id = key
        self.lesson_workspace.refresh()
        window = cast(Any, self.window())
        assessed = bool(c.reports_enabled or self.service.grading.works(key))
        if hasattr(window, "course_journal"):
            window.course_journal.setTabVisible(
                window.course_journal.indexOf(self.grading), assessed
            )
        if hasattr(window, "calculation_pane"):
            window.tabs.setTabVisible(
                window.tabs.indexOf(window.calculation_pane),
                assessed,
            )
        self.summary.setText(
            f"{c.duration} последовательные пары · одна регистрация на занятие · темы общие для всех групп"
        )
        fill_table(
            self.topics,
            [(str(t.number), t.title) for t in self.api.list_topics(key)],
            [t.id for t in self.api.list_topics(key)],
        )
        rosters = {r.id: r for r in self.api.list_rosters(key)}
        fill_table(
            self.members,
            [
                (
                    r.name,
                    ", ".join(map(str, r.group_ids)),
                    str(len(self.service.redesign._roster_contexts(r))),
                )
                for r in rosters.values()
            ],
            list(rosters),
        )
        topics = {t.id: t for t in self.api.list_topics(key)}
        rows = []
        self.lesson_ids = []
        for lesson in sorted(
            self.api.list_lessons(key),
            key=lambda lesson: (
                self.service.redesign.get_block(lesson.block_id).date,
                self.service.redesign.get_block(lesson.block_id).start_slot,
            ),
        ):
            b = self.service.redesign.get_block(lesson.block_id)
            rows.append(
                (
                    str(b.date),
                    f"{b.start_slot}–{b.start_slot + c.duration - 1}",
                    f"{topics[lesson.topic_id].number}. {topics[lesson.topic_id].title}",
                    rosters[b.roster_id].name,
                    b.room,
                )
            )
            self.lesson_ids.append(lesson.id)
        fill_table(self.lessons, rows, self.lesson_ids)
        topics = self.api.list_topics(key)
        students = self.api.list_students(key)
        self.journal.setColumnCount(5 + len(topics))
        self.journal.setHorizontalHeaderLabels(
            ["ФИО", "Группа", "Подгруппа", "Специальность", "Профиль"]
            + [f"{t.number}. {t.title}" for t in topics]
        )
        projection = self.api.attendance_projection(key)
        fill_table(
            self.journal,
            [
                (
                    s.full_name,
                    s.group_number or "",
                    s.subgroup or "",
                    s.specialty or "",
                    s.profile or "",
                    *["✓" if (s.student_id, t.id) in projection else "" for t in topics],
                )
                for s in students
            ],
            [s.student_id for s in students],
        )
        for row, s in enumerate(students):
            for col, t in enumerate(topics, 5):
                item = self.journal.item(row, col)
                assert item is not None
                item.setData(Qt.ItemDataRole.UserRole, (s.student_id, t.id))
        for column in range(1, 5):
            self.journal.setColumnHidden(column, True)
        self.journal.setSortingEnabled(True)
        self.journal.sortItems(self.journal_sort.currentData(), Qt.SortOrder.AscendingOrder)
        self.filter_journal()
        self.sync_lesson_context()

    def export_journal(self):
        self.api.get_course(self.current_course_id)
        self.grading.export()

    def new_course(self):
        d = FormDialog("Новый семестровый курс", self)
        discipline = combo([(x.name, x.id) for x in self.service.list_disciplines()])
        year = combo([(str(y.start_year), y.id) for y in self.service.redesign.list_years()])
        semester, study = spin(1, 2, 1), spin(1, 6, 1)
        kind = combo(
            [
                ("Лекции", "lecture"),
                ("Лабораторные", "lab"),
                ("Семинары", "seminar"),
                ("Другое", "other"),
            ]
        )
        duration = spin(1, 2, 1)
        name = QLineEdit()
        reports = QCheckBox("Доклад включён")
        for label, w in [
            ("Название", name),
            ("Дисциплина", discipline),
            ("Учебный год", year),
            ("Семестр", semester),
            ("Курс обучения", study),
            ("Формат", kind),
            ("Длительность, пар", duration),
            ("", reports),
        ]:
            d.form.addRow(label, w)
        self.run(
            d,
            lambda: self.api.save_course(
                discipline.currentData(),
                year.currentData(),
                semester.value(),
                study.value(),
                kind.currentData(),
                duration.value(),
                name.text(),
                reports.isChecked(),
            ),
        )

    def copy_course(self):
        if self.current_course_id is None:
            return
        key = self.current_course_id
        d = FormDialog("Копия курса без истории занятий", self)
        year = combo([(str(y.start_year), y.id) for y in self.service.redesign.list_years()])
        semester = spin(1, 2, 2)
        name = QLineEdit(self.api.get_course(key).name)
        d.form.addRow("Название", name)
        d.form.addRow("Год", year)
        d.form.addRow("Семестр", semester)
        self.run(
            d, lambda: self.api.copy_course(key, year.currentData(), semester.value(), name.text())
        )

    def topic_dialog(self):
        key = self.current_course_id
        if key is None:
            return
        d = FormDialog("Тема программы курса", self)
        number = spin(1, 999, len(self.api.list_topics(key)) + 1)
        title = QLineEdit()
        row = self.topics.currentRow()
        if row >= 0:
            number_item, title_item = self.topics.item(row, 0), self.topics.item(row, 1)
            assert number_item is not None and title_item is not None
            number.setValue(int(number_item.text()))
            title.setText(title_item.text())
        d.form.addRow("Номер", number)
        d.form.addRow("Общая тема", title)
        self.run(d, lambda: self.api.save_topic(key, number.value(), title.text()))

    def groups_dialog(self, partial=False):
        key = self.current_course_id
        if key is None:
            return
        c = self.api.get_course(key)
        d = FormDialog("Подгруппа курса" if partial else "Добавить годовые группы", self)
        groups = choices(
            [
                (f"{g.specialty_name} · группа {g.number}", g.id)
                for g in self.service.redesign.list_groups(c.academic_year_id)
                if g.course == c.study_course
            ]
        )
        d.form.addRow("Группы", groups)
        name = QLineEdit()
        d.form.addRow("Название состава", name)
        students = choices(
            [
                (f"{s.full_name} · гр. {s.group_number}", s.student_id)
                for s in self.service.redesign.list_student_contexts(c.academic_year_id)
                if s.course == c.study_course
            ]
        )
        if partial:
            d.form.addRow("Студенты одной группы", students)
        self.run(
            d,
            lambda: self.api.add_groups(
                key, checked(groups), name.text() or None, checked(students) if partial else None
            ),
        )

    def date_slot(self, d):
        on = QDateEdit(QDate.currentDate())
        on.setCalendarPopup(True)
        c = self.api.get_course(self.current_course_id)
        slot = combo(
            [
                (f"{i + 1}. {SLOTS[i][0]}–{SLOTS[i + c.duration - 1][1]}", i + 1)
                for i in range(9 - c.duration)
            ]
        )
        d.form.addRow("Дата", on)
        d.form.addRow("Пары", slot)
        return on, slot

    def schedule_dialog(self):
        key = self.current_course_id
        if key is None:
            return
        d = FormDialog("Запланировать занятия", self)
        roster = combo([(r.name, r.id) for r in self.api.list_rosters(key)])
        topic = combo([(f"{t.number}. {t.title}", t.number) for t in self.api.list_topics(key)])
        d.form.addRow("Состав", roster)
        d.form.addRow("Первая тема", topic)
        on, slot = self.date_slot(d)
        interval = combo([("Одно занятие", 0), ("Каждые 7 дней", 7), ("Каждые 14 дней", 14)])
        count = spin(1, 100, 1)
        room = QLineEdit()
        d.form.addRow("Повторение", interval)
        d.form.addRow("Количество тем", count)
        d.form.addRow("Аудитория", room)

        def save():
            args = (key, roster.currentData())
            if interval.currentData():
                self.api.schedule_series(
                    *args,
                    on.date().toPython(),
                    slot.currentData(),
                    interval.currentData(),
                    topic.currentData(),
                    count.value(),
                    room.text(),
                )
            else:
                self.api.schedule(
                    *args,
                    topic.currentData(),
                    on.date().toPython(),
                    slot.currentData(),
                    room.text(),
                )

        self.run(d, save)

    def selected_lesson(self):
        return selected_key(self.lessons)

    def move_dialog(self):
        key = self.selected_lesson()
        if key is None:
            return
        d = FormDialog("Перенос одного занятия", self)
        on, slot = self.date_slot(d)
        self.run(d, lambda: self.api.move_instance(key, on.date().toPython(), slot.currentData()))

    def recurrence_dialog(self):
        key = self.selected_lesson()
        if key is None:
            return
        lesson = self.api.get_lesson(key)
        if lesson.series_id is None:
            return
        d = FormDialog("Изменить регулярность с даты", self)
        on, _ = self.date_slot(d)
        interval = combo([("7 дней", 7), ("14 дней", 14)])
        d.form.addRow("Интервал", interval)
        self.run(
            d,
            lambda: self.api.change_recurrence(
                lesson.series_id, on.date().toPython(), interval.currentData()
            ),
        )

    def view_lesson(self):
        key = self.selected_lesson()
        if key is None:
            return
        lesson = self.api.get_lesson(key)
        b = self.service.redesign.get_block(lesson.block_id)
        self.lesson_workspace.select_lesson(b.pair_ids[0])
        window = cast(Any, self.window())
        if hasattr(window, "tabs"):
            window.tabs.setCurrentWidget(self.lesson_workspace)

    def mark_rectangle(self):
        key = self.current_course_id
        selected = {
            i.data(Qt.ItemDataRole.UserRole)
            for i in self.journal.selectedItems()
            if i.column() >= 5
        }
        if not selected:
            return
        d = FormDialog("Посещение выбранных тем", self)
        rosters = {r.id: r for r in self.api.list_rosters(key)}
        targets = {}
        topics = {t.id: t for t in self.api.list_topics(key)}
        for topic_id in sorted({t for _, t in selected}, key=lambda t: topics[t].number):
            targets[topic_id] = combo(
                [
                    (
                        f"{self.service.redesign.get_block(lesson.block_id).date} · {rosters[self.service.redesign.get_block(lesson.block_id).roster_id].name}",
                        lesson.id,
                    )
                    for lesson in self.api.list_lessons(key)
                    if lesson.topic_id == topic_id
                ]
            )
            d.form.addRow(f"{topics[topic_id].number}. {topics[topic_id].title}", targets[topic_id])
        reason = QLineEdit()
        d.form.addRow("Причина", reason)
        self.run(
            d,
            lambda: self.api.mark_topics(
                key,
                selected,
                {t: control.currentData() for t, control in targets.items()},
                reason.text(),
            ),
        )

    def sync_lesson_context(self, *_):
        key = self.current_course_id
        if key is None:
            return
        block_id = self.lesson_workspace.viewed_block_id
        lesson = next(
            (lesson for lesson in self.api.list_lessons(key) if lesson.block_id == block_id), None
        )
        self.grading.set_context(key, lesson.id if lesson else None)

    def update_attendance(self, pair_ids=None):
        if self.current_course_id is None:
            return
        projection = self.api.attendance_projection(self.current_course_id, pair_ids)
        affected = None
        if pair_ids is not None:
            affected = {
                lesson.topic_id
                for lesson in self.api.list_lessons(self.current_course_id)
                if set(self.service.redesign.get_block(lesson.block_id).pair_ids).intersection(
                    pair_ids
                )
            }
        for row in range(self.journal.rowCount()):
            for col in range(5, self.journal.columnCount()):
                item = self.journal.item(row, col)
                if item is not None:
                    student, topic = item.data(Qt.ItemDataRole.UserRole)
                    if affected is None or topic in affected:
                        item.setText("✓" if (student, topic) in projection else "")

    def settings_dialog(self):
        if self.current_course_id is None:
            return
        course = self.api.get_course(self.current_course_id)
        dialog = FormDialog("Возможности и настройки курса", self)
        name = QLineEdit(course.name)
        reports = QCheckBox("Доклад включён")
        reports.setChecked(course.reports_enabled)
        dialog.form.addRow("Название", name)
        dialog.form.addRow("", reports)
        self.run(
            dialog,
            lambda: self.api.save_course(
                course.discipline_id,
                course.academic_year_id,
                course.semester,
                course.study_course,
                course.kind,
                course.duration,
                name.text(),
                reports.isChecked(),
                course.id,
            ),
        )

    def add_written(self):
        if self.current_course_id is None:
            return
        guarded(self, self.grading.add_work)
        self.show_course()

    def filter_journal(self, *_):
        query = self.journal_search.text().casefold().replace("ё", "е")
        for row in range(self.journal.rowCount()):
            values = []
            for col in range(5):
                item = self.journal.item(row, col)
                if item:
                    values.append(item.text())
            self.journal.setRowHidden(
                row, query not in " ".join(values).casefold().replace("ё", "е")
            )

    def delete_selected(self):
        selected = self.selected_lesson()
        if selected is None:
            return
        occurrence = self.api.get_lesson(selected)
        block = self.service.redesign.get_block(occurrence.block_id)
        delete_lesson_dialog(self, self.service, block.pair_ids[0], self.changed)
