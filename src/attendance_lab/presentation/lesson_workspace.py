"""Operator lesson workspace: viewed block is independent from active registration."""

from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.course_absences import expected_student_ids
from attendance_lab.application.journal import DEFAULT_REASON, JOURNAL_REASONS, TOGGLE_MESSAGES
from attendance_lab.domain.redesign import EXCUSED_ABSENCE_REASON
from attendance_lab.presentation.attendance_models import (
    IDENTITY,
    StudentModel,
    StudentProxy,
    StudentSearch,
)
from attendance_lab.presentation.attendance_reasons import MANUAL_REASONS
from attendance_lab.presentation.common import (
    bind_table_clear,
    guarded,
    plain_label,
    private_message,
    search_field,
)
from attendance_lab.presentation.lesson_filtering import BoundedComboBox, roster_caption
from attendance_lab.presentation.lesson_picker import LessonPicker, LessonPickerOption
from attendance_lab.presentation.preparation import FormDialog
from attendance_lab.presentation.search_results import SearchResultsPane
from attendance_lab.presentation.table_columns import TableColumns
from attendance_lab.presentation.toolbars import office_toolbar


class LessonWorkspace(QWidget):
    def __init__(self, service, controller, changed, parent=None, *, open_lesson=None):
        super().__init__(parent)
        self.service, self.api, self.controller, self.changed = (
            service,
            service.redesign,
            controller,
            changed,
        )
        self.selected_course_id = None
        self.viewed_block_id = None
        self.viewed_lesson_id = None
        self.open_standalone = open_lesson or self._open_standalone
        self.pending_uids = []
        self.pinned_uid = None
        self.correction_card = None
        self.suspended_binding = None
        self.last_card_uid = None
        self.last_card_name = None
        self.last_presented_uid = None
        self.last_presented_name = None
        self.last_presented_kind = None
        self.registration_context = (None, None)
        self.search_expanded = False
        self.manual_mode = False
        self.saved_query = ""
        self.evidence_changed: Callable[..., None] = lambda *_: None
        self.binding_changed: Callable[[str], None] = lambda _: self.evidence_changed()
        layout = QVBoxLayout(self)
        self._roster_filter_choices = {}
        self._roster_filter_course = None
        self.roster_filter_row = QWidget()
        roster_filter_layout = QHBoxLayout(self.roster_filter_row)
        roster_filter_layout.setContentsMargins(0, 0, 0, 0)
        roster_filter_layout.addWidget(plain_label("Подгруппа"))
        self.roster_filter = BoundedComboBox()
        self.roster_filter.setToolTip(
            "Выберите сохранённую подгруппу, чтобы сократить список занятий"
        )
        roster_filter_layout.addWidget(self.roster_filter, 1)
        self.roster_filter_row.hide()
        layout.addWidget(self.roster_filter_row)
        bar = QHBoxLayout()
        self.blocks = LessonPicker[int | str]()
        self.today = QCheckBox("Только сегодня")
        self.today.setChecked(True)
        bar.addWidget(plain_label("Занятие"))
        bar.addWidget(self.blocks, 1)
        bar.addWidget(self.today)
        layout.addLayout(bar)
        registration_actions = office_toolbar(self)
        self.open_button = QPushButton("Открыть регистрацию")
        self.open_button.setToolTip("Открыть регистрацию выбранного занятия · F9")
        self.open_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.open_button.clicked.connect(self.open_selected)
        registration_actions.addWidget(self.open_button)
        self.stop_button = QPushButton("Завершить")
        self.stop_button.setToolTip("Завершить текущую регистрацию · Shift+F9")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self.close_active)
        registration_actions.addWidget(self.stop_button)
        self.expand_button = QPushButton("Расширить на обе пары")
        self.expand_button.clicked.connect(self.expand)
        self.expand_button.hide()
        self.manual_button = QPushButton("Ручная отметка")
        self.manual_button.setToolTip("Отметить посещение выбранного студента · F2")
        self.manual_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.manual_button.clicked.connect(self.manual_current)
        registration_actions.addWidget(self.manual_button)
        self.correct_button = QPushButton("Исправить карту")
        self.correct_button.setEnabled(False)
        self.correct_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        )
        self.correct_button.clicked.connect(self.correct_last_card)
        registration_actions.addWidget(self.correct_button)
        layout.addWidget(registration_actions)
        self.scope = plain_label(wrap=True)
        layout.addWidget(self.scope)
        self.search_button = QPushButton("Сопоставление карт")
        self.search_button.clicked.connect(self.open_search)
        splitter = QSplitter()
        splitter.setMinimumHeight(260)
        roster = QWidget()
        roster_layout = QVBoxLayout(roster)
        self.model, self.proxy = StudentModel(), StudentProxy()
        self.expected_by_pair = {}
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setToolTip("Delete или Backspace — отменить ручные отметки выбранных студентов.")
        bind_table_clear(self.table, self.clear_selected_attendance)
        self.roster_search = search_field("ФИО, группа или № зачётки · F3")
        self.roster_search.textChanged.connect(self.filter_students)
        self.roster_search.returnPressed.connect(self.focus_first_result)
        roster_layout.addWidget(self.roster_search)
        self.show_context = QCheckBox("Подробнее о студенте")
        self.table_columns = TableColumns(
            self.table,
            "lesson",
            keys=(*self.model.keys, "attendance"),
            available=lambda field: (
                (field != "subgroup" or not self.is_lecture())
                and (
                    field not in ("record_book_number", "course", "specialty", "profile")
                    or self.show_context.isChecked()
                )
            ),
        )
        self.table.doubleClicked.connect(self.toggle_student_attendance)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.attendance_menu)
        self.table_shortcuts = []
        for key in ("Return", "Enter", "Space"):
            shortcut = QShortcut(QKeySequence(key), self.table)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda: self.toggle_student_attendance(self.table.currentIndex())
            )
            self.table_shortcuts.append(shortcut)
        roster_layout.addWidget(self.table)
        self.show_context.toggled.connect(self.table_columns.restore_visibility)
        roster_layout.addWidget(self.show_context)
        self.table_columns.restore_visibility()
        selection_actions = office_toolbar(self)
        self.evidence_button = QPushButton("История отметки")
        self.evidence_button.clicked.connect(self.show_evidence)
        selection_actions.addWidget(self.evidence_button)
        roster_layout.addWidget(selection_actions)
        self.counts = plain_label()
        roster_layout.addWidget(self.counts)
        splitter.addWidget(roster)
        matching = QWidget()
        match_layout = QVBoxLayout(matching)
        match_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.uid_label = plain_label(
            "Сопоставление: откройте регистрацию и приложите карту", wrap=True
        )
        self.uid_label.setObjectName("ActiveBanner")
        self.uid_label.setMinimumHeight(24)
        self.queue = QListWidget()
        self.queue.setMaximumHeight(60)
        self.queue.setMinimumHeight(34)
        self.queue.currentItemChanged.connect(
            lambda item, previous: self.pin(item) if item else None
        )
        match_layout.addWidget(self.queue)
        self.course = QComboBox()
        self.course.addItem("Все курсы", None)
        for course in range(1, 7):
            self.course.addItem(f"{course} курс", course)
        match_layout.addWidget(self.course)
        self.course.currentIndexChanged.connect(self.find)
        self.card_search = SearchResultsPane()
        self.search = self.card_search.search
        self.search.setPlaceholderText("ФИО, группа или № зачётки")
        self.matches = self.card_search.results
        self.matches.setMinimumHeight(80)
        match_layout.addWidget(self.card_search, 1)
        self.confirm_button = QPushButton("Привязать карту · Enter")
        self.confirm_button.clicked.connect(self.confirm_match)
        self.binding_result = plain_label(wrap=True)
        self.binding_result.hide()
        self.binding_target = plain_label(wrap=True)
        self.binding_target.hide()
        self.unbind_button = QPushButton("Снять привязку…")
        self.unbind_button.clicked.connect(self.unbind_selected_card)
        self.unbind_button.hide()
        self.defer_button = QPushButton("Отложить · Esc")
        self.defer_button.clicked.connect(self.defer)
        self.last_card_label = plain_label("Последнее предъявление: пока нет", wrap=True)
        self.last_card_label.setFixedHeight(44)
        self.feedback = plain_label(wrap=True)
        match_layout.addWidget(self.feedback)
        match_scroll = QScrollArea()
        match_scroll.setWidgetResizable(True)
        match_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        match_scroll.setWidget(matching)
        self.match_scroll = match_scroll
        match_scroll.viewport().installEventFilter(self)
        for field in (self.queue, self.course, self.search, self.matches):
            field.installEventFilter(self)
        for listing in (self.queue, self.matches):
            listing.currentItemChanged.connect(
                lambda *_, field=listing: (
                    self.reveal_matching_field(field) if field.hasFocus() else None
                )
            )
        matching_column = QWidget()
        column_layout = QVBoxLayout(matching_column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(3)
        column_layout.addWidget(self.last_card_label)
        column_layout.addWidget(self.search_button)
        column_layout.addWidget(self.uid_label)
        column_layout.addWidget(match_scroll, 1)
        column_layout.addWidget(self.binding_target)
        column_layout.addWidget(self.confirm_button)
        column_layout.addWidget(self.unbind_button)
        column_layout.addWidget(self.binding_result)
        column_layout.addWidget(self.defer_button)
        self.idle_space = QWidget()
        column_layout.addWidget(self.idle_space, 1)
        splitter.addWidget(matching_column)
        splitter.setSizes([650, 320])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.roster_filter.currentIndexChanged.connect(self.filter_roster)
        self.blocks.currentIndexChanged.connect(self.show_block)
        self.today.toggled.connect(self.refresh)
        self.search.textChanged.connect(self.find)
        self.cache = StudentSearch(())
        self.card_search.activation.connect(self.confirm_match)
        self.table.selectionModel().selectionChanged.connect(self.update_action_state)
        self.matches.currentItemChanged.connect(self.update_action_state)
        self.matches.currentItemChanged.connect(self.select_search_student)
        self.matches.itemClicked.connect(self.select_search_student)
        self.model.modelReset.connect(self.update_action_state)
        self.update_matching_visibility()
        self.update_action_state()

    def open_search(self):
        self.search_expanded = True
        self.update_matching_visibility()
        for region in (self, self.match_scroll.widget(), self.card_search):
            layout = region.layout() if region is not None else None
            if layout is not None:
                layout.activate()
        self.search.setFocus()
        self.reveal_matching_field(self.search)

    def update_matching_visibility(self):
        expanded = bool(self.pinned_uid or self.manual_mode or self.search_expanded)
        for widget in (self.uid_label, self.match_scroll, self.confirm_button, self.defer_button):
            widget.setVisible(expanded)
        self.queue.setVisible(len(self.pending_uids) > 1)
        self.search_button.setVisible(not expanded)
        self.idle_space.setVisible(not expanded)
        self.update_action_state()

    def eventFilter(self, watched, event):
        if watched is self.match_scroll.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self, self.reveal_focused_matching_field)
        if event.type() == QEvent.Type.FocusIn and watched in (
            self.queue,
            self.course,
            self.search,
            self.matches,
        ):
            self.reveal_matching_field(watched)
        return super().eventFilter(watched, event)

    def reveal_focused_matching_field(self):
        field = QApplication.focusWidget()
        if field in (self.queue, self.course, self.search, self.matches):
            self.reveal_matching_field(field)

    def reveal_matching_field(self, field):
        if isinstance(field, QListWidget):
            item = field.currentItem()
            if item:
                field.scrollToItem(item)
            rect = field.visualItemRect(item) if item else field.viewport().rect()
            content = self.match_scroll.widget()
            assert content is not None
            point = field.viewport().mapTo(content, rect.center() if item else rect.topLeft())
            self.match_scroll.ensureVisible(
                point.x(), point.y(), 0, max(12, rect.height() // 2) if item else 12
            )
        else:
            self.match_scroll.ensureWidgetVisible(field, 0, 0)
            top = field.mapTo(self.match_scroll.viewport(), field.rect().topLeft()).y()
            bottom = top + field.height()
            scrollbar = self.match_scroll.verticalScrollBar()
            if top < 0:
                scrollbar.setValue(scrollbar.value() + top)
            elif bottom > self.match_scroll.viewport().height():
                scrollbar.setValue(
                    scrollbar.value() + bottom - self.match_scroll.viewport().height()
                )

    def is_lecture(self):
        return (
            self.selected_course_id is not None
            and self.service.courses.get_course(self.selected_course_id).kind == "lecture"
        )

    def manual_allowed(self, pair_ids):
        if not pair_ids:
            return False
        active = set()
        if self.controller.active_session_id is not None:
            session = self.api.get_session(self.controller.active_session_id)
            if session.closed_at is None:
                active.update(self.api.get_block(session.block_id).pair_ids)
        elif self.controller.active_lesson_id is not None:
            active.add(self.controller.active_lesson_id)
        now = datetime.now(UTC)
        return all(
            pair in active or self.service.get_lesson(pair).ends_at <= now for pair in pair_ids
        )

    def update_action_state(self, *_):
        if not hasattr(self, "confirm_button"):
            return
        pairs = self.viewed_pair_ids()
        selected = self.selected_ids()
        self.search_button.setEnabled(bool(self.cache.students))
        self.open_button.setEnabled(bool(pairs) and self.controller.active_lesson_id not in pairs)
        self.stop_button.setEnabled(
            self.controller.active_lesson_id is not None
            or self.controller.active_session_id is not None
        )
        manual_enabled = self.manual_allowed(pairs)
        self.manual_button.setEnabled(manual_enabled and bool(self.manual_target_ids()))
        self.manual_button.setToolTip(
            "Отметить посещение выбранного студента · F2"
            if self.manual_button.isEnabled()
            else "Сначала выберите студента в таблице или результатах поиска"
            if manual_enabled
            else "Для будущего занятия сначала откройте регистрацию"
            if pairs
            else "Сначала выберите занятие"
        )
        has_evidence = bool(pairs and selected) and any(
            evidence.student_id in selected
            for pair in pairs
            for evidence in self.api.pair_evidence(pair, include_excused=True)
        )
        self.evidence_button.setEnabled(has_evidence)
        self.evidence_button.setToolTip(
            "Посмотреть предъявления и ручные отметки выбранных студентов"
            if has_evidence
            else "Выберите студента с сохранённой отметкой"
        )
        matching_target = (
            self.manual_allowed(self.manual_pair_ids) if self.manual_mode else bool(self.pinned_uid)
        )
        current = self.matches.currentItem()
        self.confirm_button.setEnabled(current is not None and matching_target)
        card_work = bool(self.pinned_uid) and not self.manual_mode
        self.binding_target.setVisible(card_work)
        self.binding_target.setText(
            f"Выбран студент: {current.text()}" if current else "Выберите студента в результатах."
        )
        self.unbind_button.setVisible(card_work and self.correction_card is not None)
        self.defer_button.setEnabled(
            bool(self.manual_mode or self.pinned_uid or self.search_expanded)
        )
        self.defer_button.setText(
            "Отменить ручную отметку · Esc"
            if self.manual_mode
            else "Отменить исправление · Esc"
            if self.correction_card is not None
            else "Отложить карту · Esc"
            if self.pinned_uid
            else "Закрыть поиск · Esc"
        )

    def filter_roster(self, *_):
        if self._roster_filter_course is not None:
            self._roster_filter_choices[self._roster_filter_course] = (
                self.roster_filter.currentData()
            )
        self.refresh()

    def refresh_roster_filter(self):
        course_id = self.selected_course_id
        visible = course_id is not None and not self.is_lecture()
        self.roster_filter_row.setVisible(visible)
        selected = self._roster_filter_choices.get(course_id)
        self.roster_filter.blockSignals(True)
        self.roster_filter.clear()
        self.roster_filter.addItem("Все подгруппы", None)
        if visible:
            course = self.service.courses.get_course(course_id)
            groups = self.api.list_groups(course.academic_year_id)
            rosters = self.service.courses.list_rosters(course_id)
            for roster in sorted(rosters, key=lambda value: (value.name.casefold(), value.id)):
                caption = roster_caption(roster, groups)
                self.roster_filter.addItem(caption, roster.id)
                self.roster_filter.setItemData(
                    self.roster_filter.count() - 1, caption, Qt.ItemDataRole.ToolTipRole
                )
        index = self.roster_filter.findData(selected)
        self.roster_filter.setCurrentIndex(max(0, index))
        self._roster_filter_course = course_id
        self.roster_filter.blockSignals(False)
        return self.roster_filter.currentData() if visible else None

    def refresh(self, *_):
        self.cache = StudentSearch(self.api.list_student_contexts())
        selected = self.blocks.currentData()
        roster_filter = self.refresh_roster_filter()
        self.blocks.blockSignals(True)
        blocks = self.api.list_blocks()
        titles = {}
        if self.selected_course_id is not None:
            topics = {
                topic.id: f"{topic.number}. {topic.title}"
                for topic in self.service.courses.list_topics(self.selected_course_id)
            }
            titles = {
                lesson.block_id: topics.get(lesson.topic_id)
                for lesson in self.service.courses.list_lessons(self.selected_course_id)
            }
            blocks = [block for block in blocks if block.id in titles]
        if roster_filter is not None:
            blocks = [block for block in blocks if block.roster_id == roster_filter]
        paired = {pair for block in blocks for pair in block.pair_ids}
        disciplines = {d.id: d.name for d in self.service.list_disciplines()}
        rosters = {r.id: r for r in self.api.list_rosters()}
        groups = self.api.list_groups()
        lessons = self.service.list_lessons()
        by_id = {p.id: p for p in lessons}
        entries: list[tuple[datetime, LessonPickerOption[int | str]]] = []
        is_lecture = self.is_lecture()
        for block in blocks:
            if (
                self.today.isChecked()
                and block.date != datetime.now(ZoneInfo(block.timezone)).date()
            ):
                continue
            roster = rosters[block.roster_id]
            first, last = by_id[block.pair_ids[0]], by_id[block.pair_ids[-1]]
            zone = ZoneInfo(block.timezone)
            title = titles.get(block.id) or block.title or "Занятие"
            times = (
                f"{first.starts_at.astimezone(zone):%H:%M}–{last.ends_at.astimezone(zone):%H:%M}"
            )
            label = f"{block.date:%d.%m.%Y} · {times}"
            roster_name = "Общий поток" if is_lecture else roster_caption(roster, groups)
            if self.selected_course_id is not None and not is_lecture:
                label += f" · {roster_name}"
            label += f" · {title}"
            if self.selected_course_id is None:
                label += f" · {disciplines[roster.discipline_id]}"
            entries.append(
                (
                    first.starts_at,
                    LessonPickerOption(
                        block.id,
                        block.date,
                        times,
                        title,
                        roster_name,
                        first.state,
                        label,
                    ),
                )
            )
        for lesson in lessons:
            if self.selected_course_id is not None:
                continue
            if lesson.id in paired:
                continue
            if (
                self.today.isChecked()
                and lesson.starts_at.astimezone(ZoneInfo(lesson.timezone)).date()
                != datetime.now(ZoneInfo(lesson.timezone)).date()
            ):
                continue
            zone = ZoneInfo(lesson.timezone)
            starts = lesson.starts_at.astimezone(zone)
            times = f"{starts:%H:%M}–{lesson.ends_at.astimezone(zone):%H:%M}"
            title = lesson.title or disciplines[lesson.discipline_id]
            entries.append(
                (
                    lesson.starts_at,
                    LessonPickerOption(
                        f"lesson:{lesson.id}",
                        starts.date(),
                        times,
                        title,
                        "",
                        lesson.state,
                        f"{starts:%d.%m.%Y} · {times} · {title}",
                    ),
                )
            )
        self.blocks.replace_choices(
            (entry for _, entry in sorted(entries, key=lambda item: item[0])),
            selected,
            select_first=True,
        )
        self.blocks.setEnabled(bool(entries))
        self.blocks.blockSignals(False)
        if self.blocks.currentData() != selected:
            self.blocks.currentIndexChanged.emit(self.blocks.currentIndex())
        else:
            self.show_block()
        self.find()

    def show_block(self, *_):
        selected = self.blocks.currentData()
        self.viewed_block_id = selected if isinstance(selected, int) else None
        self.viewed_lesson_id = int(selected.split(":")[1]) if isinstance(selected, str) else None
        students = {}
        self.expected_by_pair = {}
        pair_ids = ()
        self.cache = StudentSearch(self.api.list_student_contexts())
        if self.viewed_block_id is not None:
            block = self.api.get_block(self.viewed_block_id)
            roster = next((r for r in self.api.list_rosters() if r.id == block.roster_id), None)
            if roster:
                self.cache = StudentSearch(
                    self.api.list_student_contexts(
                        roster.academic_year_id, roster.discipline_id, roster.semester
                    )
                )
            pair_ids = block.pair_ids
            if self.selected_course_id is not None:
                expected = expected_student_ids(self.service, self.selected_course_id, block.id)
                students.update(
                    (context.student_id, context)
                    for context in self.cache.students
                    if context.student_id in expected
                )
                self.expected_by_pair = {pair: set(expected) for pair in block.pair_ids}
            else:
                for pair in block.pair_ids:
                    contexts = self.api.get_pair_contexts(pair)
                    self.expected_by_pair[pair] = {s.student_id for s in contexts}
                    students.update((s.student_id, s) for s in contexts)
            course_name = (
                self.service.courses.get_course(self.selected_course_id).name
                if self.selected_course_id is not None
                else next(
                    d.name
                    for d in self.service.list_disciplines()
                    if roster and d.id == roster.discipline_id
                )
            )
            if self.is_lecture():
                self.scope.setText("Поток · посещение за занятие")
            else:
                name = (
                    roster.name
                    if roster
                    and not roster.name.startswith(
                        ("Исторический состав", "Восстановленный состав")
                    )
                    else ""
                )
                self.scope.setText(
                    (name + " · " if name else "")
                    + ("Две пары · одна отметка" if len(pair_ids) == 2 else "Посещение за занятие")
                )
            self.scope.setToolTip(
                f"{course_name}\n{self.blocks.currentText()}"
                + (f" · ауд. {block.room}" if block.room else "")
            )
        elif self.viewed_lesson_id is not None:
            pair_ids = (self.viewed_lesson_id,)
            expected = self.service.get_lesson_roster(self.viewed_lesson_id)
            self.expected_by_pair[self.viewed_lesson_id] = expected
            students.update(
                (s.student_id, s) for s in self.cache.students if s.student_id in expected
            )
            self.scope.setText("Посещение за занятие")
        else:
            self.scope.setText(
                "Сегодня занятий нет. Снимите «Только сегодня», чтобы выбрать другую дату."
                if self.today.isChecked()
                else "Занятий пока нет. Добавьте занятие в разделе «Курс»."
            )
        for pair in pair_ids:
            for evidence in self.api.pair_evidence(pair):
                if (
                    evidence.student_id is None
                    and evidence.uid
                    and evidence.uid not in self.pending_uids
                ):
                    self.pending_uids.append(evidence.uid)
                    self.queue.addItem(evidence.uid)
        if self.pinned_uid is None and self.pending_uids and not self.manual_mode:
            self.pinned_uid = self.pending_uids[0]
            self.uid_label.setText("Сопоставление карты: " + self.pinned_uid)
        self.update_last_card()
        self.model.whole_block = True
        self.model.set_students(
            students.values(), pair_ids[:1] if self.model.whole_block else pair_ids
        )
        self.table_columns.restore_visibility()
        self.filter_students()
        self.update_counts()
        self.find()

    def viewed_pair_ids(self):
        if self.viewed_block_id is not None:
            return self.api.get_block(self.viewed_block_id).pair_ids
        return (self.viewed_lesson_id,) if self.viewed_lesson_id is not None else ()

    def select_lesson(self, lesson_id):
        block = next((b for b in self.api.list_blocks() if lesson_id in b.pair_ids), None)
        key = block.id if block else f"lesson:{lesson_id}"
        if block and self.roster_filter.currentData() not in (None, block.roster_id):
            index = self.roster_filter.findData(block.roster_id)
            self.roster_filter.setCurrentIndex(max(0, index))
        if self.blocks.findData(key) < 0 and self.today.isChecked():
            self.today.setChecked(False)
        index = self.blocks.findData(key)
        if index >= 0:
            unchanged = self.blocks.currentIndex() == index
            self.blocks.setCurrentIndex(index)
            if unchanged:
                self.blocks.currentIndexChanged.emit(index)

    def update_counts(self):
        self.expand_button.setEnabled(self.controller.active_session_id is not None)
        pair_ids = self.viewed_pair_ids()
        if not pair_ids:
            self.counts.setText("Выберите занятие")
            self.update_action_state()
            return
        zone = ZoneInfo(self.service.get_lesson(pair_ids[0]).timezone)
        values = {}
        people = {s.student_id: s for s in self.cache.students}
        listed = {s.student_id for s in self.model.students}
        outside = {}
        unknown = set()
        for pair in pair_ids:
            for evidence in self.api.pair_evidence(pair, include_excused=True):
                if not evidence.active:
                    continue
                if evidence.student_id is None:
                    if evidence.uid:
                        unknown.add(evidence.uid)
                    continue
                key = (evidence.student_id, pair_ids[0] if self.model.whole_block else pair)
                if evidence.kind == "excused":
                    values.setdefault(key, "У")
                    continue
                if evidence.student_id in people and evidence.student_id not in listed:
                    outside[evidence.student_id] = people[evidence.student_id]
                if values.get(key) in (None, "У") or evidence.kind != "manual":
                    values[key] = (
                        "Ручное подтверждение " if evidence.kind == "manual" else "Карта "
                    ) + evidence.at.astimezone(zone).strftime("%H:%M")
        if outside:
            self.model.append_students(outside.values())
        self.model.update_attendance(values)
        marked = {student for (student, _), status in values.items() if status != "У"}
        self.counts.setText(
            f"Отмечено: {len(marked)}" + (f" · ожидают привязки: {len(unknown)}" if unknown else "")
        )
        expected = set().union(*self.expected_by_pair.values()) if self.expected_by_pair else set()
        self.counts.setToolTip(
            f"В исходном списке занятия: {len(expected)}. В таблице: {len(self.model.students)}. "
            "Предъявления студентов вне исходного списка также сохранены."
        )
        self.update_action_state()

    def selected_ids(self):
        return {i.data(IDENTITY) for i in self.table.selectionModel().selectedRows()}

    def open_selected(self):
        self.update_action_state()
        if not self.open_button.isEnabled():
            return
        if self.viewed_lesson_id is not None:
            self.open_standalone(self.viewed_lesson_id)
            self.search.setFocus() if self.course.currentData() is not None else self.course.setFocus()
            return
        if self.viewed_block_id is None:
            self.scope.setText("Выберите занятие. Для других дат снимите «Только сегодня».")
            return
        block = self.api.get_block(self.viewed_block_id)
        guarded(
            self,
            lambda: (self.controller.open_session(block.id, set(block.pair_ids)), self.changed()),
        )
        if self.controller.active_session_id is not None:
            self.search.setFocus() if self.course.currentData() is not None else self.course.setFocus()

    def _open_standalone(self, lesson_id):
        guarded(self, lambda: (self.controller.open_lesson(lesson_id), self.changed()))

    def close_active(self):
        self.update_action_state()
        if not self.stop_button.isEnabled():
            return
        guarded(self, lambda: (self.controller.close_session(), self.changed()))

    def expand(self):
        guarded(self, lambda: (self.controller.expand_session(), self.changed()))

    def focus_roster_search(self):
        self.roster_search.setFocus()
        self.roster_search.selectAll()

    def filter_students(self, *_):
        query = self.roster_search.text().strip()
        self.proxy.filter_by_ids(
            {student.student_id for student in self.cache.find(query)} if query else None
        )
        self.table_columns.schedule_fit()

    def focus_first_result(self):
        if self.proxy.rowCount():
            self.table.setCurrentIndex(self.proxy.index(0, 0))
            self.table.setFocus()

    def mark_student(self, index, reason=DEFAULT_REASON):
        student_id = index.data(IDENTITY) if index.isValid() else None
        if not student_id:
            return
        pairs = self.viewed_pair_ids()
        if not self.manual_allowed(pairs):
            self.scope.setText("Для ручной отметки будущего занятия сначала откройте регистрацию.")
            return
        evidence = [
            item for pair in pairs for item in self.api.evidence(student_id, pair) if item.active
        ]
        if any(item.kind != "excused" for item in evidence) or (
            evidence and reason == EXCUSED_ABSENCE_REASON
        ):
            self.scope.setText("Отметка уже сохранена.")
            return
        batch = guarded(
            self,
            lambda: self.api.create_manual_batch({(student_id, pair) for pair in pairs}, reason),
        )
        if batch is not None:
            self.update_counts()
            self.evidence_changed()
            self.scope.setText(
                "Отсутствие по уважительной причине отмечено."
                if reason == EXCUSED_ABSENCE_REASON
                else "Посещение отмечено."
            )

    def toggle_student_attendance(self, index):
        student_id = index.data(IDENTITY) if index.isValid() else None
        pairs = self.viewed_pair_ids()
        if not student_id or not pairs:
            return
        mode = guarded(
            self,
            lambda: self.api.toggle_attendance(
                student_id, pairs, allow_new=self.manual_allowed(pairs)
            ),
        )
        if mode in ("mark", "revoke", "revoke_card"):
            self.update_counts()
            self.evidence_changed()
        if isinstance(mode, str):
            self.scope.setText(TOGGLE_MESSAGES[mode])

    def clear_selected_attendance(self):
        students = self.selected_ids()
        pairs = self.viewed_pair_ids()
        if not students or not pairs:
            return
        changed, mode = self.api.clear_attendance(students, pairs)
        if changed:
            self.update_counts()
            self.evidence_changed()
        self.scope.setText(TOGGLE_MESSAGES[mode])

    def attendance_menu(self, position):
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        self.table.setCurrentIndex(index)
        menu = QMenu(self.table)
        for reason in JOURNAL_REASONS:
            title = "Отметить присутствие" if reason == DEFAULT_REASON else reason.capitalize()
            menu.addAction(title, lambda value=reason: self.mark_student(index, value))
        menu.addSeparator()
        menu.addAction("История отметки", self.show_evidence)
        menu.exec(self.table.viewport().mapToGlobal(position))
        menu.deleteLater()

    def find(self, *_):
        if not hasattr(self, "matches"):
            return
        self.matches.clear()
        course = self.course.currentData()
        roster_ids = {p.student_id for p in self.model.students}
        for student in sorted(
            [
                s
                for s in self.cache.find(self.search.text())
                if course is None or s.course == course
            ],
            key=lambda s: s.student_id not in roster_ids,
        )[:80]:
            details = [student.full_name]
            if student.group_number is not None:
                details.append(f"гр. {student.group_number}")
            if student.record_book_number:
                details.append(student.record_book_number)
            item = QListWidgetItem(" · ".join(details), self.matches)
            item.setToolTip(
                " · ".join(
                    str(value)
                    for value in (
                        student.full_name,
                        student.record_book_number,
                        student.specialty,
                        student.profile,
                        "" if self.is_lecture() else getattr(student, "subgroup", ""),
                    )
                    if value
                )
            )
            item.setData(IDENTITY, student.student_id)
        self.matches.setCurrentRow(-1)
        self.update_action_state()

    def scan(self, scan, result):
        if result.kind in ("success", "repeat"):
            if self.is_active_scan(scan):
                self.last_card_uid = scan.uid
                self.last_card_name = result.full_name
                self.last_presented_uid = scan.uid
                self.last_presented_name = result.full_name
                self.last_presented_kind = result.kind
        if result.full_name is None and result.kind in ("success", "repeat"):
            self.binding_result.hide()
            if scan.uid not in self.pending_uids:
                self.pending_uids.append(scan.uid)
                self.queue.addItem(scan.uid)
            if self.pinned_uid is None and not self.manual_mode:
                self.pinned_uid = scan.uid
                self.uid_label.setText("Карта " + scan.uid + " · не привязана")
            elif self.pinned_uid != scan.uid and not self.manual_mode:
                self.feedback.setText(
                    f"Карта {scan.uid} сохранена в очереди. Сейчас сопоставляется {self.pinned_uid}."
                )
        self.update_last_card()
        self.update_counts()

    def is_active_scan(self, scan):
        return self.is_active_context(scan.lesson_id, scan.session_id)

    def is_active_context(self, lesson_id, session_id):
        return (
            session_id == self.controller.active_session_id
            if session_id is not None
            else self.controller.active_session_id is None
            and lesson_id == self.controller.active_lesson_id
        )

    def pin(self, item):
        if not self.manual_mode:
            if self.pinned_uid != item.text():
                self.matches.setCurrentRow(-1)
            self.pinned_uid = item.text()
            self.correction_card = None
            self.suspended_binding = None
            self.confirm_button.setText("Привязать карту · Enter")
            self.uid_label.setText("Карта " + self.pinned_uid + " · не привязана")
            self.update_action_state()

    def update_last_card(self):
        self.update_matching_visibility()
        self.correct_button.setEnabled(self.last_card_uid is not None)
        target = (
            f"Карта {self.last_card_uid} · {self.last_card_name or 'Не сопоставлена'}"
            if self.last_card_uid
            else "Пока нет карты для исправления"
        )
        self.correct_button.setToolTip(target + " · F4")
        if (
            self.last_card_uid
            and self.last_presented_uid
            and self.last_card_uid != self.last_presented_uid
        ):
            name = self.correct_button.fontMetrics().elidedText(
                self.last_card_name or f"карту {self.last_card_uid}",
                Qt.TextElideMode.ElideRight,
                180,
            )
            self.correct_button.setText(f"Исправить {name} · F4")
        else:
            self.correct_button.setText("Исправить карту")
        if self.last_presented_kind == "display_error":
            text = "Ошибка обновления экрана\nПроверьте отметку в журнале"
            background, foreground = "#fee2e2", "#991b1b"
        elif self.last_presented_kind == "error":
            text = "Последнее предъявление: ошибка\nНе удалось сохранить отметку"
            background, foreground = "#fee2e2", "#991b1b"
        elif self.last_presented_uid is None:
            text = "Последнее предъявление: пока нет"
            background, foreground = "#e8f0fa", "#234667"
        elif self.last_presented_name:
            state = "уже отмечен" if self.last_presented_kind == "repeat" else "отметка сохранена"
            text = (
                f"Последний студент: {self.last_presented_name}\n"
                f"Карта {self.last_presented_uid} привязана · {state}"
            )
            background, foreground = "#dcfce7", "#14532d"
        else:
            state = (
                "ожидает в очереди" if self.last_presented_uid in self.pending_uids else "отложена"
            )
            text = f"Последняя карта: {self.last_presented_uid}\nКарта не привязана · {state}"
            background, foreground = "#fef3c7", "#713f12"
        self.last_card_label.setText(text)
        style = (
            f"background: {background}; color: {foreground}; padding: 8px; "
            "border-radius: 6px; font-weight: 600;"
        )
        if self.last_card_label.styleSheet() != style:
            self.last_card_label.setStyleSheet(style)
        if self.last_presented_name and self.last_presented_uid:
            current = next(
                (c for c in self.service.list_cards() if c.uid == self.last_presented_uid), None
            )
            if current and current.student_id:
                count = sum(c.student_id == current.student_id for c in self.service.list_cards())
                if count > 1:
                    self.last_card_label.setText(
                        text + f"\nУ студента несколько карт: {count}. Проверьте привязки."
                    )

    def sync_registration_context(self):
        session = self.controller.active_session_id
        context = (session, None if session is not None else self.controller.active_lesson_id)
        if context != self.registration_context:
            self.registration_context = context
            self.last_card_uid = None
            self.last_card_name = None
            self.last_presented_uid = None
            self.last_presented_name = None
            self.last_presented_kind = None
            self.update_last_card()

    def scan_failed(self):
        self.last_card_uid = None
        self.last_card_name = None
        self.last_presented_uid = None
        self.last_presented_name = None
        self.last_presented_kind = "error"
        self.update_last_card()

    def display_update_error(self):
        self.last_card_uid = None
        self.last_card_name = None
        self.last_presented_uid = None
        self.last_presented_name = None
        self.last_presented_kind = "display_error"
        self.update_last_card()

    def correct_last_card(self):
        uid = self.last_card_uid
        card = next((card for card in self.service.list_cards() if card.uid == uid), None)
        if card is None:
            return
        self.manual_mode = False
        if self.pinned_uid and self.pinned_uid != card.uid and self.suspended_binding is None:
            current = self.matches.currentItem()
            self.suspended_binding = (
                self.pinned_uid,
                self.search.text(),
                self.search.cursorPosition(),
                self.course.currentData(),
                current.data(IDENTITY) if current else None,
            )
        self.pinned_uid = card.uid
        self.correction_card = card if card.student_id is not None else None
        self.course.setCurrentIndex(0)
        self.search.clear()
        self.matches.setCurrentRow(-1)
        self.binding_result.hide()
        self.uid_label.setText(f"Карта {card.uid} · {card.full_name or 'не привязана'}")
        self.confirm_button.setText(
            "Изменить привязку · Enter" if self.correction_card else "Привязать карту · Enter"
        )
        self.open_search()

    def unbind_selected_card(self):
        card = self.correction_card
        if card is None or card.student_id is None:
            return
        if private_message(
            self,
            "Снять привязку карты?",
            f"Карта {card.uid} будет отвязана от студента {card.full_name}. "
            f"Отметки этой карты на занятиях ({card.lesson_count}) останутся без владельца.",
            question=True,
        ):

            def unbind():
                self.service.unbind_card(card.uid, expected_student_id=card.student_id)
                self.defer()
                self.binding_changed(card.uid)

            guarded(self, unbind)

    def card_binding_changed(self, uid):
        card = next((c for c in self.service.list_cards() if c.uid == uid), None)
        if card is None:
            return
        if uid == self.last_card_uid:
            self.last_card_name = card.full_name
        if uid == self.last_presented_uid:
            self.last_presented_name = card.full_name
        if card.student_id is not None:
            self.pending_uids = [pending for pending in self.pending_uids if pending != uid]
            if self.pinned_uid == uid:
                self.pinned_uid = None
                self.matches.setCurrentRow(-1)
        elif uid not in self.pending_uids:
            self.pending_uids.append(uid)
        self.queue.blockSignals(True)
        self.queue.clear()
        self.queue.addItems(self.pending_uids)
        if self.pinned_uid is None and self.pending_uids and not self.manual_mode:
            self.pinned_uid = self.pending_uids[0]
            self.matches.setCurrentRow(-1)
        if self.pinned_uid in self.pending_uids:
            self.queue.setCurrentRow(self.pending_uids.index(self.pinned_uid))
        self.queue.blockSignals(False)
        if not self.manual_mode:
            self.uid_label.setText(
                "Сопоставление карты: " + self.pinned_uid
                if self.pinned_uid
                else "Нет несопоставленных карт"
            )
        self.update_last_card()
        self.feedback.setText(
            "Привязка исправлена; история обновлена."
            if card.student_id
            else f"Привязка карты {uid} снята. "
            + (
                "Выберите правильное ФИО — повторно прикладывать карту не нужно."
                if self.pinned_uid == uid
                else "Для сопоставления сначала выберите эту карту в очереди."
            )
        )

    def start_manual(self):
        if self.viewed_pair_ids() and not self.manual_allowed(self.viewed_pair_ids()):
            self.scope.setText("Для ручной отметки будущего занятия сначала откройте регистрацию.")
            self.update_action_state()
            return
        if not self.viewed_pair_ids():
            private_message(self, "Ручная отметка", "Сначала выберите просматриваемое занятие.")
            return
        if not self.manual_mode:
            self.saved_query = self.search.text()
            self.saved_cursor = self.search.cursorPosition()
            self.manual_pair_ids = tuple(self.viewed_pair_ids())
        self.manual_mode = True
        self.update_matching_visibility()
        self.uid_label.setText(
            "Ручная отметка: "
            + self.service.lifecycle.describe(self.manual_pair_ids[0])
            + "\nВыберите ФИО и нажмите Enter"
        )
        self.confirm_button.setText("Отметить посещение · Enter")
        self.search.clear()
        self.course.setFocus() if self.course.currentData() is None else self.search.setFocus()

    def confirm_match(self):
        if self.manual_mode and not self.manual_allowed(self.manual_pair_ids):
            self.feedback.setText("Регистрация закрыта. Отметка будущего занятия пока недоступна.")
            self.update_action_state()
            return
        item = self.matches.currentItem()
        if item is None:
            self.feedback.setText("Явно выберите студента в результатах поиска.")
            return
        student_id = item.data(IDENTITY)
        student_label = item.text()

        def apply():
            if self.manual_mode:
                self.api.create_manual_batch(
                    {(student_id, pair) for pair in self.manual_pair_ids},
                    "Оператор подтвердил присутствие в выбранном занятии",
                )
                self.manual_mode = False
                self.confirm_button.setText("Привязать карту · Enter")
                self.search.setText(self.saved_query)
                self.search.setCursorPosition(self.saved_cursor)
                self.search.setFocus()
                self.feedback.setText("Ручная отметка сохранена; время подтверждения записано.")
                if self.pinned_uid is None and self.pending_uids:
                    self.pinned_uid = self.pending_uids[0]
                self.uid_label.setText(
                    "Сопоставление карты: " + self.pinned_uid
                    if self.pinned_uid
                    else "Нет несопоставленных карт"
                )
            elif self.pinned_uid:
                bound_uid = self.pinned_uid
                previous = self.correction_card
                if previous is not None:
                    if previous.student_id == student_id:
                        self.feedback.setText("Карта уже привязана к этому студенту.")
                        return
                    if not private_message(
                        self,
                        "Изменить привязку карты?",
                        f"Текущий владелец: {previous.full_name}. Новый: {item.text()}. "
                        f"Изменится принадлежность сохранённых отметок на занятиях ({previous.lesson_count}).",
                        question=True,
                    ):
                        return
                self.service.bind_card(
                    bound_uid,
                    student_id,
                    correction=previous is not None,
                    expected_student_id=previous.student_id if previous else None,
                )
                self.last_card_uid = self.pinned_uid
                self.last_card_name = next(
                    (s.full_name for s in self.cache.students if s.student_id == student_id), None
                )
                if bound_uid == self.last_presented_uid:
                    self.last_presented_name = self.last_card_name
                self.update_last_card()
                self.feedback.setText("Карта сопоставлена; сохранённые предъявления раскрыты.")
                resume_pending = self.suspended_binding is not None
                self.defer()
                if not resume_pending:
                    self.search.clear()
                self.binding_result.setText(
                    f"Карта {bound_uid} привязана: {self.last_card_name or student_label}."
                )
                self.binding_result.show()
                self.binding_changed(bound_uid)
                self.search.setFocus()
            else:
                self.feedback.setText("Сначала откройте регистрацию и приложите карту студента.")
            self.update_counts()
            self.evidence_changed()

        guarded(self, apply)

    def defer(self):
        self.search_expanded = False
        if self.manual_mode:
            self.manual_mode = False
            self.confirm_button.setText("Привязать карту · Enter")
            self.search.setText(self.saved_query)
            self.search.setCursorPosition(self.saved_cursor)
            self.search.setFocus()
        elif self.suspended_binding is not None:
            uid, query, cursor, course, student = self.suspended_binding
            self.suspended_binding = None
            self.correction_card = None
            self.pinned_uid = uid
            self.confirm_button.setText("Привязать карту · Enter")
            self.course.setCurrentIndex(self.course.findData(course))
            self.search.setText(query)
            self.search.setCursorPosition(cursor)
            self.matches.setCurrentRow(-1)
            for row in range(self.matches.count()):
                if self.matches.item(row).data(IDENTITY) == student:
                    self.matches.setCurrentRow(row)
                    break
            self.search.setFocus()
        elif self.pinned_uid:
            if self.pinned_uid in self.pending_uids:
                self.pending_uids.remove(self.pinned_uid)
            self.correction_card = None
            self.confirm_button.setText("Привязать карту · Enter")
            self.queue.clear()
            self.queue.addItems(self.pending_uids)
            self.pinned_uid = self.pending_uids[0] if self.pending_uids else None
            self.matches.setCurrentRow(-1)
        self.uid_label.setText(
            "Сопоставление карты: " + self.pinned_uid
            if self.pinned_uid
            else "Нет несопоставленных карт"
        )
        self.update_last_card()

    def manual_target_ids(self):
        selected = self.selected_ids()
        if selected:
            return selected
        item = self.matches.currentItem()
        if self.search_expanded and not self.pinned_uid and not self.manual_mode and item:
            return {item.data(IDENTITY)}
        return set()

    def select_search_student(self, item, *_):
        if self.search_expanded and not self.pinned_uid and not self.manual_mode and item:
            self.table.clearSelection()

    def manual_current(self):
        self.manual_block(student_ids=self.manual_target_ids())

    def manual_block(self, *, student_ids=None):
        ids = self.selected_ids() if student_ids is None else set(student_ids)
        pairs = self.viewed_pair_ids()
        if not ids or not pairs:
            return
        if not self.manual_allowed(pairs):
            self.scope.setText("Для ручной отметки будущего занятия сначала откройте регистрацию.")
            self.update_action_state()
            return
        targets = {(student, pair) for student in ids for pair in pairs}
        names = "\n".join(s.full_name for s in self.cache.students if s.student_id in ids)
        reason, ok = QInputDialog.getItem(
            self,
            "Ручная отметка выбранных",
            f"{self.blocks.currentText()}\n{names}\nСтудентов: {len(ids)} · посещение всего занятия\nПричина:",
            MANUAL_REASONS,
            0,
            False,
        )
        if ok:
            guarded(
                self,
                lambda: (
                    self.api.create_manual_batch(targets, reason),
                    self.update_counts(),
                    self.evidence_changed(),
                ),
            )

    def show_evidence(self):
        ids = self.selected_ids()
        pairs = self.viewed_pair_ids()
        if not ids or not pairs:
            return
        dialog = FormDialog("Основания присутствия", self)
        listing = QListWidget()
        names = {student.student_id: student.full_name for student in self.cache.students}
        for student in sorted(ids):
            for pair in pairs:
                for evidence in self.api.pair_evidence(pair, include_excused=True):
                    if evidence.student_id != student:
                        continue
                    timezone = ZoneInfo(self.service.get_lesson(pair).timezone)
                    source = {
                        "excused": "Уважительная причина",
                        "manual": "Ручная отметка",
                    }.get(evidence.kind, "Карта")
                    status = (
                        f"отменено {evidence.revoked_at.astimezone(timezone):%d.%m %H:%M}"
                        if evidence.revoked_at
                        else "действует"
                        if evidence.active
                        else "отменено"
                    )
                    item = QListWidgetItem(
                        f"{names.get(student, 'Студент')} · {source} · "
                        f"{evidence.at.astimezone(timezone):%d.%m %H:%M} · {status}",
                        listing,
                    )
                    if evidence.active and evidence.batch_id is not None:
                        item.setData(IDENTITY, (evidence.batch_id, student))
        dialog.form.addRow(listing)

        def revoke():
            item = listing.currentItem()
            if item and item.data(IDENTITY) is not None:
                batch_id, student = item.data(IDENTITY)
                self.api.revoke_manual_targets({(batch_id, student, pair) for pair in pairs})
                self.update_counts()
                self.evidence_changed()
                dialog.accept()

        revoke_button = QPushButton("Отменить ручную отметку")
        revoke_button.setEnabled(False)
        revoke_button.clicked.connect(lambda: guarded(dialog, revoke))
        listing.currentItemChanged.connect(
            lambda item, previous: revoke_button.setEnabled(
                item is not None and item.data(IDENTITY) is not None
            )
        )
        dialog.form.addRow(revoke_button)
        dialog.finish()
        dialog.exec()
