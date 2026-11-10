"""Course centred programme, membership and schedule commands."""

from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QSpinBox,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from attendance_lab.application.course_absences import course_absences
from attendance_lab.application.redesign import SLOTS
from attendance_lab.presentation.attendance_reasons import MANUAL_REASONS
from attendance_lab.presentation.common import (
    fill_table,
    guarded,
    plain_label,
    selected_key,
    table,
)
from attendance_lab.presentation.course_journal import AbsenceCountItem, update_topic_cell
from attendance_lab.presentation.course_membership import edit_line_groups_dialog
from attendance_lab.presentation.grading import GradingPane
from attendance_lab.presentation.journal_marking import mark_journal_cells
from attendance_lab.presentation.lesson_lifecycle import (
    delete_lesson_dialog,
    restore_lessons_dialog,
)
from attendance_lab.presentation.preparation import FormDialog, checked, choices, combo, spin
from attendance_lab.presentation.programme_topics import (
    ProgrammeTopicChoice,
    build_add_topics_dialog,
    build_lesson_topic_dialog,
    build_topic_dialog,
)
from attendance_lab.presentation.toolbars import office_toolbar, tool_action, tool_menu


class CourseWorkspace(QWidget):
    def __init__(self, service, changed, lesson_workspace, preparation, parent=None, *, files=None):
        super().__init__(parent)
        self.service, self.api, self.changed = service, service.courses, changed
        self.evidence_changed: Callable[[], None] = self.update_attendance
        self.lesson_workspace = lesson_workspace
        self.journal_pane = None
        outer = QVBoxLayout(self)
        self.courses = QComboBox()
        outer.addWidget(self.courses)
        self.tools = {}
        toolbar = office_toolbar(self)
        self.tools["new_course"] = tool_action(
            toolbar, "Новый курс", QStyle.StandardPixmap.SP_FileIcon, self.new_course
        )
        self.tools["settings"] = tool_action(
            toolbar,
            "Настройки",
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            self.settings_dialog,
        )
        more = tool_menu(toolbar)
        self.tools["copy"] = more.addAction("Копировать в другой семестр…", self.copy_course)
        self.tools["written"] = more.addAction("Добавить письменную работу…", self.add_written)
        outer.addWidget(toolbar)
        self.summary = plain_label(wrap=True)
        outer.addWidget(self.summary)
        self.tabs = QTabWidget()
        programme = QWidget()
        layout = QVBoxLayout(programme)
        toolbar = office_toolbar(self)
        self.tools["add_topic"] = tool_action(
            toolbar,
            "Добавить тему",
            QStyle.StandardPixmap.SP_FileIcon,
            lambda: self.topic_dialog(False),
        )
        self.tools["edit_topic"] = tool_action(
            toolbar,
            "Изменить название",
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            self.topic_dialog,
        )
        layout.addWidget(toolbar)
        self.tools["add_topics"] = tool_action(
            toolbar,
            "Добавить темы…",
            QStyle.StandardPixmap.SP_FileDialogNewFolder,
            self.add_topics_dialog,
        )
        self.topics = table(["№", "Тема"])
        layout.addWidget(self.topics)
        self.topics.itemDoubleClicked.connect(lambda *_: self.topic_dialog())
        members = QWidget()
        layout = QVBoxLayout(members)
        toolbar = office_toolbar(self)
        self.tools["groups"] = tool_action(
            toolbar, "Добавить группы", QStyle.StandardPixmap.SP_DirIcon, self.groups_dialog
        )
        self.tools["subgroup"] = tool_action(
            toolbar,
            "Подгруппа",
            QStyle.StandardPixmap.SP_FileDialogNewFolder,
            lambda: self.groups_dialog(True),
        )
        self.tools["edit_groups"] = tool_action(
            toolbar,
            "Изменить состав…",
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            lambda: edit_line_groups_dialog(
                self, self.service, self.current_course_id, selected_key(self.members), self.changed
            ),
        )
        layout.addWidget(toolbar)
        self.members = table(["Состав", "Группы", "Студентов"])
        self.members.itemSelectionChanged.connect(self.update_actions)
        layout.addWidget(self.members)
        schedule = QWidget()
        layout = QVBoxLayout(schedule)
        toolbar = office_toolbar(self)
        self.tools["schedule"] = tool_action(
            toolbar, "Добавить занятие", QStyle.StandardPixmap.SP_FileIcon, self.schedule_dialog
        )
        self.tools["view"] = tool_action(
            toolbar, "Открыть", QStyle.StandardPixmap.SP_DialogOpenButton, self.view_lesson
        )
        self.tools["lesson_topic"] = tool_action(
            toolbar,
            "Тема занятия…",
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            self.lesson_topic_dialog,
        )
        self.tools["move"] = tool_action(
            toolbar, "Перенести", QStyle.StandardPixmap.SP_ArrowRight, self.move_dialog
        )
        more = tool_menu(toolbar)
        self.tools["recurrence"] = more.addAction("Изменить регулярность…", self.recurrence_dialog)
        self.tools["delete"] = more.addAction("Удалить выбранное занятие…", self.delete_selected)
        self.tools["restore"] = more.addAction(
            "Удалённые занятия…", lambda: restore_lessons_dialog(self, self.service, self.changed)
        )
        layout.addWidget(toolbar)
        self.lessons = table(["Дата", "Пары", "Тема", "Состав", "Аудитория"])
        layout.addWidget(self.lessons)
        self.lessons.itemDoubleClicked.connect(lambda *_: self.view_lesson())
        self.tabs.addTab(programme, "Программа")
        self.tabs.addTab(members, "Состав")
        self.tabs.addTab(schedule, "Расписание")
        self.journal = table(["ФИО"])
        self.journal.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.journal.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.journal.itemDoubleClicked.connect(self.mark_clicked_cell)
        self.mark_shortcuts = []
        for key in ("Space", "Return", "Enter"):
            shortcut = QShortcut(QKeySequence(key), self.journal)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.mark_rectangle)
            self.mark_shortcuts.append(shortcut)
        jp = QWidget()
        self.journal_pane = jp
        jl = QVBoxLayout(jp)
        toolbar = office_toolbar(self)
        self.tools["mark"] = tool_action(
            toolbar,
            "Поставить посещение",
            QStyle.StandardPixmap.SP_DialogApplyButton,
            self.mark_rectangle,
            "Поставить посещение в выбранной ячейке · Enter или Пробел",
        )
        self.journal_reason = combo([(reason, reason) for reason in MANUAL_REASONS])
        self.journal_reason.setAccessibleName("Причина ручной отметки")
        self.journal_reason.setToolTip("Причина ручной отметки в выбранных ячейках")
        toolbar.addWidget(self.journal_reason)
        self.tools["export"] = tool_action(
            toolbar,
            "Экспорт",
            QStyle.StandardPixmap.SP_DialogSaveButton,
            lambda: guarded(self, self.export_journal),
            "Сохранить журнал в XLSX",
        )
        jl.addWidget(toolbar)
        filters = QHBoxLayout()
        self.journal_search = QLineEdit()
        self.journal_search.setPlaceholderText("ФИО, группа, подгруппа, специальность")
        self.journal_search.textChanged.connect(self.filter_journal)
        filters.addWidget(self.journal_search)
        self.journal_context = QCheckBox("Группы и специальности")
        self.journal_context.toggled.connect(self.show_journal_context)
        self.journal_sort = combo(
            [("По ФИО", 0), ("По группе", 1), ("По подгруппе", 2), ("По специальности", 3)]
        )
        self.journal_sort.currentIndexChanged.connect(
            lambda: self.journal.sortItems(
                self.journal_sort.currentData(), Qt.SortOrder.AscendingOrder
            )
        )
        self.journal_status = combo([("Все студенты", False), ("С пропусками", True)])
        self.journal_status.currentIndexChanged.connect(self.filter_journal)
        filters.addWidget(self.journal_status)
        self.absence_threshold = QSpinBox()
        self.absence_threshold.setRange(1, 999)
        self.absence_threshold.setPrefix("От ")
        self.absence_threshold.setToolTip("Минимальное число пропущенных тем")
        self.absence_threshold.setMaximumWidth(90)
        self.absence_threshold.valueChanged.connect(self.filter_journal)
        filters.addWidget(self.absence_threshold)
        more = tool_menu(toolbar, "Вид")
        for widget in (self.journal_context, self.journal_sort):
            action = QWidgetAction(more)
            action.setDefaultWidget(widget)
            more.addAction(action)
        jl.addLayout(filters)
        self.journal_notice = plain_label(wrap=True)
        self.journal_notice.setObjectName("Muted")
        self.journal_notice.hide()
        jl.addWidget(self.journal_notice)
        jl.addWidget(self.journal)
        self.tabs.addTab(jp, "Посещения")
        self.grading = GradingPane(service, files=files, parent=self)

        self.lessons.itemSelectionChanged.connect(
            lambda: self.grading.set_context(self.current_course_id, self.selected_lesson())
        )
        self.tabs.addTab(preparation, "Учебный список и каталоги")
        outer.addWidget(self.tabs)
        self.topics.itemSelectionChanged.connect(self.update_actions)
        self.lessons.itemSelectionChanged.connect(self.update_actions)
        self.journal.itemSelectionChanged.connect(self.update_actions)
        self.courses.currentIndexChanged.connect(self.show_course)
        self.update_actions()
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

    def run(self, d, save, *, select_saved_course=False):
        d.finish()
        if d.exec() == QDialog.DialogCode.Accepted:

            def apply():
                saved = save()
                self.changed()
                self.refresh()
                if select_saved_course:
                    self.courses.setCurrentIndex(self.courses.findData(saved.id))

            guarded(self, apply)

    def show_course(self, *_):
        key = self.current_course_id
        if key is None:
            self.summary.setText("Создайте курс, затем добавьте темы и учебные группы.")
            for listing in (self.topics, self.members, self.lessons, self.journal):
                listing.setRowCount(0)
            self.grading.set_context(None, None)
            self.update_actions()
            return
        c = self.api.get_course(key)
        self.grading.set_context(key, None, refresh=True)
        self.lesson_workspace.selected_course_id = key
        self.lesson_workspace.refresh()
        self.summary.setText(
            f"{'Лекции · общий поток' if c.kind == 'lecture' else 'Общая программа для всех групп'} · {c.duration} {'пара' if c.duration == 1 else 'пары'} на занятие"
        )
        fill_table(
            self.topics,
            [(str(t.number), t.title) for t in self.api.list_topics(key)],
            [t.id for t in self.api.list_topics(key)],
        )
        rosters = {r.id: r for r in self.api.list_rosters(key)}
        groups = {g.id: g for g in self.service.redesign.list_groups(c.academic_year_id)}
        if c.kind == "lecture":
            stream = self.api.list_students(key)
            group_ids = {student.group_id for student in stream}
            member_rows = [
                (
                    "Общий поток",
                    "; ".join(
                        f"{group.specialty_name} · гр. {group.number}"
                        for group in groups.values()
                        if group.id in group_ids
                    ),
                    len(stream),
                )
            ]
            member_keys = [key]
        else:
            member_rows = [
                (
                    r.name,
                    "; ".join(
                        f"{groups[g].specialty_name} · гр. {groups[g].number}"
                        for g in r.group_ids
                        if g in groups
                    ),
                    len(self.service.redesign._roster_contexts(r)),
                )
                for r in rosters.values()
            ]
            member_keys = list(rosters)
        fill_table(self.members, member_rows, member_keys)
        for row in range(self.members.rowCount()):
            item = self.members.item(row, 1)
            assert item is not None
            item.setToolTip(item.text())
        self.members.horizontalHeader().setStretchLastSection(False)
        self.members.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.members.setColumnWidth(2, 90)
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
                    f"{b.date:%d.%m.%Y}",
                    f"{SLOTS[b.start_slot - 1][0]}–{SLOTS[b.start_slot + c.duration - 2][1]}",
                    f"{topics[lesson.topic_id].number}. {topics[lesson.topic_id].title}",
                    "Общий поток" if c.kind == "lecture" else rosters[b.roster_id].name,
                    b.room,
                )
            )
            self.lesson_ids.append(lesson.id)
        fill_table(self.lessons, rows, self.lesson_ids)
        self.refresh_journal()
        self.lessons.setHorizontalHeaderLabels(
            ["Дата", "Время", "Тема", "Поток" if c.kind == "lecture" else "Состав", "Ауд."]
        )
        self.lessons.setColumnHidden(3, c.kind == "lecture")
        self.members.setHorizontalHeaderLabels(
            ["Поток" if c.kind == "lecture" else "Состав", "Группы", "Студентов"]
        )
        self.lessons.horizontalHeader().setStretchLastSection(False)
        self.lessons.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.lessons.setColumnWidth(4, 64)
        self.topics.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.sync_lesson_context()
        self.update_actions()

    def refresh_journal(self):
        key = self.current_course_id
        if key is None:
            return
        topics = self.api.list_topics(key)
        students = self.api.list_students(key)
        self.journal_observed_only = not students
        if self.journal_observed_only:
            students = self.api.list_observed_students(key)
        name_width = self.journal.columnWidth(0) if self.journal.rowCount() else 300
        self.journal.setColumnCount(6 + len(topics))
        self.journal.setHorizontalHeaderLabels(
            ["ФИО", "Группа", "Подгруппа", "Специальность", "Профиль"]
            + [str(t.number) for t in topics]
            + ["Пропуски"]
        )
        projection = self.api.attendance_projection(key)
        self.refresh_absences(projection)
        fill_table(
            self.journal,
            [
                (
                    s.full_name,
                    s.group_number or "",
                    s.subgroup or "",
                    s.specialty or "",
                    s.profile or "",
                    *("" for _ in topics),
                    len(self.missed_topics.get(s.student_id, set())),
                )
                for s in students
            ],
            [s.student_id for s in students],
        )
        for row, s in enumerate(students):
            self.journal.setItem(
                row,
                self.journal.columnCount() - 1,
                self.absence_item(s.student_id),
            )
            name_item = self.journal.item(row, 0)
            assert name_item is not None
            name_item.setToolTip(s.full_name)
            for col, t in enumerate(topics, 5):
                item = self.journal.item(row, col)
                assert item is not None
                item.setData(Qt.ItemDataRole.UserRole, (s.student_id, t.id))
                update_topic_cell(
                    item,
                    s.full_name,
                    t,
                    present=(s.student_id, t.id) in projection,
                    missed=t.id in self.missed_topics.get(s.student_id, set()),
                    observed_only=self.journal_observed_only,
                )
        self.show_journal_context()
        self.journal.horizontalHeader().setStretchLastSection(False)
        self.journal.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.journal.setColumnWidth(0, name_width)
        self.journal.setColumnWidth(1, 70)
        for column, t in enumerate(topics, 5):
            self.journal.setColumnWidth(column, 44)
            header = self.journal.horizontalHeaderItem(column)
            assert header is not None
            header.setToolTip(f"Тема {t.number}: {t.title}")
        self.journal.setColumnWidth(self.journal.columnCount() - 1, 80)
        self.journal_status.setEnabled(not self.journal_observed_only)
        if self.journal_observed_only:
            self.journal_status.blockSignals(True)
            self.journal_status.setCurrentIndex(0)
            self.journal_status.blockSignals(False)
        self.journal.setSortingEnabled(True)
        self.journal.sortItems(self.journal_sort.currentData(), Qt.SortOrder.AscendingOrder)
        self.filter_journal()

    def export_journal(self):
        self.api.get_course(self.current_course_id)
        self.grading.export(observed_only=self.journal_observed_only)

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
            select_saved_course=True,
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
            d,
            lambda: self.api.copy_course(key, year.currentData(), semester.value(), name.text()),
            select_saved_course=True,
        )

    def topic_dialog(self, edit=True):
        key = self.current_course_id
        if key is None:
            return
        result = build_topic_dialog(self, self.api, key, selected_key(self.topics), edit=edit)
        if result is not None:
            self.run(*result)

    def add_topics_dialog(self):
        key = self.current_course_id
        if key is not None:
            self.run(*build_add_topics_dialog(self, self.api, key))

    def lesson_topic_dialog(self):
        key = self.selected_lesson()
        if key is not None:
            self.run(*build_lesson_topic_dialog(self, self.service, key))

    def groups_dialog(self, partial=False):
        key = self.current_course_id
        if key is None:
            return
        c = self.api.get_course(key)
        if partial and c.kind == "lecture":
            return
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
        if c.kind != "lecture":
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
        slot = combo([(f"{i + 1}-я пара · {SLOTS[i][0]}", i + 1) for i in range(9 - c.duration)])
        for index in range(slot.count()):
            slot.setItemData(
                index,
                f"{SLOTS[index][0]}–{SLOTS[index + c.duration - 1][1]} · {c.duration} "
                + ("пара" if c.duration == 1 else "пары"),
                Qt.ItemDataRole.ToolTipRole,
            )
        slot.setToolTip("Выберите начальную пару. Продолжительность определена выбранным курсом.")
        d.form.addRow("Дата", on)
        d.form.addRow("Начальная пара", slot)
        return on, slot

    def schedule_dialog(self):
        key = self.current_course_id
        if key is None:
            return
        course = self.api.get_course(key)
        d = FormDialog("Запланировать занятия", self)
        formats = {
            "lecture": "Лекции",
            "lab": "Лабораторные",
            "seminar": "Семинары",
            "other": "Занятия",
        }
        d.form.addRow(
            plain_label(
                f"{course.name} · {formats[course.kind]} · {course.duration} "
                + ("пара" if course.duration == 1 else "пары"),
                wrap=True,
            )
        )
        roster = combo([(r.name, r.id) for r in self.api.list_rosters(key)])
        topic = ProgrammeTopicChoice(self.api, key, selected_topic_id=selected_key(self.topics))
        lecture = course.kind == "lecture"
        if not lecture:
            d.form.addRow("Состав", roster)
        d.form.addRow("Тема / первая тема серии", topic)
        on, slot = self.date_slot(d)
        interval = combo([("Одно занятие", 0), ("Каждые 7 дней", 7), ("Каждые 14 дней", 14)])
        count = spin(1, 100, 1)
        count.setEnabled(False)
        interval.currentIndexChanged.connect(lambda: count.setEnabled(bool(interval.currentData())))
        room = QLineEdit()
        d.form.addRow("Повторение", interval)
        count.setToolTip("Для серии заранее добавьте следующие темы в программу курса.")
        d.form.addRow("Занятий подряд по программе", count)
        d.form.addRow("Аудитория", room)

        def save():
            return self.api.schedule_with_topic(
                key,
                None if lecture else roster.currentData(),
                on.date().toPython(),
                slot.currentData(),
                interval_days=interval.currentData(),
                count=count.value(),
                room=room.text(),
                **topic.selection(),
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
        block = self.service.redesign.get_block(self.api.get_lesson(key).block_id)
        on.setDate(QDate(block.date.year, block.date.month, block.date.day))
        slot.setCurrentIndex(slot.findData(block.start_slot))
        self.run(d, lambda: self.api.move_instance(key, on.date().toPython(), slot.currentData()))

    def recurrence_dialog(self):
        key = self.selected_lesson()
        if key is None:
            return
        lesson = self.api.get_lesson(key)
        if lesson.series_id is None:
            return
        d = FormDialog("Изменить регулярность с даты", self)
        block = self.service.redesign.get_block(lesson.block_id)
        on = QDateEdit(QDate(block.date.year, block.date.month, block.date.day))
        on.setCalendarPopup(True)
        d.form.addRow("Начиная с даты", on)
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

    def mark_clicked_cell(self, item):
        if isinstance(item.data(Qt.ItemDataRole.UserRole), tuple):
            self.mark_rectangle()

    def mark_rectangle(self):
        key = self.current_course_id
        selected = {
            i.data(Qt.ItemDataRole.UserRole)
            for i in self.journal.selectedItems()
            if isinstance(i.data(Qt.ItemDataRole.UserRole), tuple)
            and not self.journal.isRowHidden(i.row())
        }
        if not selected:
            return
        guarded(
            self,
            lambda: mark_journal_cells(
                self,
                self.service,
                key,
                selected,
                self.evidence_changed,
                reason=self.journal_reason.currentText(),
            ),
        )
        self.update_actions()

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
        students = self.api.list_students(self.current_course_id)
        observed_only = not students
        if observed_only:
            students = self.api.list_observed_students(self.current_course_id)
        displayed_ids = set()
        for row in range(self.journal.rowCount()):
            identity = self.journal.item(row, 0)
            if identity is not None:
                displayed_ids.add(identity.data(Qt.ItemDataRole.UserRole))
        if (
            observed_only != self.journal_observed_only
            or {student.student_id for student in students} != displayed_ids
        ):
            self.refresh_journal()
            return
        affected = None
        if pair_ids is not None:
            affected = {
                lesson.topic_id
                for lesson in self.api.list_lessons(self.current_course_id)
                if set(self.service.redesign.get_block(lesson.block_id).pair_ids).intersection(
                    pair_ids
                )
            }
        complete_projection = self.api.attendance_projection(self.current_course_id)
        self.refresh_absences(complete_projection)
        topics = {topic.id: topic for topic in self.api.list_topics(self.current_course_id)}
        sorting = self.journal.isSortingEnabled()
        self.journal.setSortingEnabled(False)
        for row in range(self.journal.rowCount()):
            first = self.journal.item(row, 0)
            if first is None:
                continue
            student_id = first.data(Qt.ItemDataRole.UserRole)
            for col in range(5, self.journal.columnCount() - 1):
                item = self.journal.item(row, col)
                if item is not None:
                    student, topic = item.data(Qt.ItemDataRole.UserRole)
                    if affected is None or topic in affected:
                        update_topic_cell(
                            item,
                            first.text(),
                            topics[topic],
                            present=(student, topic) in complete_projection,
                            missed=topic in self.missed_topics.get(student, set()),
                            observed_only=self.journal_observed_only,
                        )
            self.journal.setItem(row, self.journal.columnCount() - 1, self.absence_item(student_id))
        self.journal.setSortingEnabled(sorting)
        self.filter_journal()

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
            identity = self.journal.item(row, 0)
            student_id = identity.data(Qt.ItemDataRole.UserRole) if identity else None
            misses = len(getattr(self, "missed_topics", {}).get(student_id, set()))
            hide = query not in " ".join(values).casefold().replace("ё", "е")
            if self.journal_status.currentData():
                hide = hide or misses < self.absence_threshold.value()
            self.journal.setRowHidden(row, hide)
        self.absence_threshold.setEnabled(bool(self.journal_status.currentData()))
        self.update_actions()

    def delete_selected(self):
        selected = self.selected_lesson()
        if selected is None:
            return
        occurrence = self.api.get_lesson(selected)
        block = self.service.redesign.get_block(occurrence.block_id)
        delete_lesson_dialog(self, self.service, block.pair_ids[0], self.changed)

    def show_journal_context(self, *_):
        lecture = (
            self.current_course_id is not None
            and self.api.get_course(self.current_course_id).kind == "lecture"
        )
        for column in (2, 3, 4):
            self.journal.setColumnHidden(
                column, not self.journal_context.isChecked() or (column == 2 and lecture)
            )
        self.journal.setColumnHidden(1, False)
        self.journal_search.setPlaceholderText(
            "ФИО, группа, специальность" if lecture else "ФИО, группа, подгруппа, специальность"
        )
        previous = self.journal_sort.currentData()
        self.journal_sort.blockSignals(True)
        self.journal_sort.clear()
        for title, column in [
            ("По ФИО", 0),
            ("По группе", 1),
            ("По подгруппе", 2),
            ("По специальности", 3),
        ]:
            if column != 2 or not lecture:
                self.journal_sort.addItem(title, column)
        self.journal_sort.setCurrentIndex(max(0, self.journal_sort.findData(previous)))
        self.journal_sort.blockSignals(False)

    def update_actions(self, *_):
        key = self.current_course_id
        valid = key is not None
        for name, action in self.tools.items():
            action.setEnabled(valid or name in ("new_course", "restore"))
        self.tools["restore"].setEnabled(bool(self.service.lifecycle.list_deletions()))
        if not valid:
            self.tools["subgroup"].setVisible(False)
            self.tools["edit_groups"].setVisible(False)
            return
        course = self.api.get_course(key)
        self.tools["groups"].setVisible(course.kind != "lecture")
        self.tools["subgroup"].setVisible(course.kind != "lecture")
        self.tools["edit_groups"].setVisible(course.kind != "lecture")
        self.tools["edit_groups"].setEnabled(
            course.kind != "lecture"
            and selected_key(self.members) in {roster.id for roster in self.api.list_rosters(key)}
        )
        self.tools["edit_topic"].setEnabled(selected_key(self.topics) is not None)
        self.tools["schedule"].setEnabled(
            bool(
                self.api.list_students(key)
                if course.kind == "lecture"
                else self.api.list_rosters(key)
            )
        )
        selected = self.selected_lesson()
        lesson = next(
            (lesson for lesson in self.api.list_lessons(key) if lesson.id == selected), None
        )
        selected = lesson.id if lesson else None
        for name in ("view", "move", "delete", "lesson_topic"):
            self.tools[name].setEnabled(selected is not None)
        self.tools["recurrence"].setEnabled(lesson is not None and lesson.series_id is not None)
        if lesson is not None:
            block = self.service.redesign.get_block(lesson.block_id)
            pairs = [self.service.get_lesson(pair) for pair in block.pair_ids]
            self.tools["move"].setEnabled(not any(pair.roster_fixed for pair in pairs))
            self.tools["move"].setToolTip(
                "Перенести выбранное занятие"
                if self.tools["move"].isEnabled()
                else "Занятие с зафиксированной историей нельзя переносить"
            )
            self.tools["delete"].setEnabled(not any(pair.state == "open" for pair in pairs))
            self.tools["delete"].setToolTip(
                "Удалить выбранное занятие"
                if self.tools["delete"].isEnabled()
                else "Сначала завершите регистрацию занятия"
            )
        selected_cells = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.journal.selectedItems()
            if isinstance(item.data(Qt.ItemDataRole.UserRole), tuple)
            and not self.journal.isRowHidden(item.row())
        ]
        scheduled_topics = {lesson.topic_id for lesson in self.api.list_lessons(key)}
        present = self.api.attendance_projection(key) if selected_cells else set()
        self.tools["mark"].setEnabled(
            bool(set(selected_cells) - present)
            and all(topic in scheduled_topics for _, topic in selected_cells)
        )
        self.tools["mark"].setToolTip(
            "Поставить посещение · Enter или Пробел; также можно дважды щёлкнуть ячейку"
            if self.tools["mark"].isEnabled()
            else "Посещение уже отмечено"
            if selected_cells and not set(selected_cells) - present
            else "Выберите ячейки тем, для которых есть занятие в расписании"
        )
        self.tools["export"].setEnabled(self.journal.rowCount() > 0)

    def refresh_absences(self, projection):
        key = self.current_course_id
        self.missed_topics = {}
        self.unresolved_topics = {}
        if key is None:
            return
        summary = course_absences(self.service, key, projection)
        self.missed_topics = dict(summary.missed_topics)
        for student, expected in summary.expected_topics.items():
            self.unresolved_topics[student] = summary.unresolved_topic_ids.difference(
                expected, {topic for sid, topic in projection if sid == student}
            )
        self.journal_notice.setVisible(summary.incomplete)
        self.journal_notice.setText(
            "У части прошедших занятий не указан состав. "
            "«—» — число пропусков неизвестно, «≥» — известно только минимальное число. "
            "Для полного расчёта нужно уточнить исторический состав этих занятий."
        )
        self.journal_status.setToolTip(
            "Пропуски только по прошедшим темам своего состава. "
            + (
                "Фильтр учитывает только пропуски с известным составом."
                if summary.incomplete
                else "Будущие темы не учитываются."
            )
        )
        if getattr(self, "journal_observed_only", False):
            self.journal_notice.setVisible(True)
            self.journal_notice.setText(
                "Состав курса ещё не задан. Показаны студенты с сохранёнными посещениями; "
                "пропуски пока не рассчитываются."
            )
            self.journal_status.setToolTip(
                "Фильтр пропусков доступен после назначения состава курса"
            )

    def absence_item(self, student_id):
        return AbsenceCountItem(
            student_id,
            self.missed_topics.get(student_id, set()),
            self.unresolved_topics.get(student_id, set()),
            expected_known=not self.journal_observed_only,
        )
