"""Operator lesson workspace: viewed block is independent from active registration."""

from collections.abc import Callable
from datetime import date
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.presentation.attendance_models import (
    IDENTITY,
    StudentModel,
    StudentProxy,
    StudentSearch,
)
from attendance_lab.presentation.common import (
    actions,
    guarded,
    lesson_label,
    plain_label,
    private_message,
)
from attendance_lab.presentation.preparation import FormDialog, checked, choices
from attendance_lab.presentation.sorting import sort_dialog


class LessonWorkspace(QWidget):
    def __init__(
        self, service, controller, changed, parent=None, *, open_lesson=None, edit_card=None
    ):
        super().__init__(parent)
        self.service, self.api, self.controller, self.changed = (
            service,
            service.redesign,
            controller,
            changed,
        )
        self.viewed_block_id = None
        self.viewed_lesson_id = None
        self.open_standalone = open_lesson or self._open_standalone
        self.pending_uids = []
        self.pinned_uid = None
        self.last_card_uid = None
        self.last_card_name = None
        self.edit_card = edit_card or (lambda _uid: False)
        self.manual_mode = False
        self.saved_query = ""
        self.evidence_changed: Callable[..., None] = lambda *_: None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.blocks = QComboBox()
        self.today = QCheckBox("Только сегодня")
        self.today.setChecked(True)
        bar.addWidget(plain_label("Занятие / блок"))
        bar.addWidget(self.blocks, 1)
        bar.addWidget(self.today)
        layout.addLayout(bar)
        registration_actions = QHBoxLayout()
        self.expand_button = QPushButton("Расширить на обе пары")
        self.expand_button.clicked.connect(self.expand)
        registration_actions.addWidget(self.expand_button)
        manual_button = QPushButton("Отметить вручную · F2")
        manual_button.clicked.connect(self.start_manual)
        registration_actions.addWidget(manual_button)
        self.correct_button = QPushButton("Исправить последнюю карту · F4")
        self.correct_button.setEnabled(False)
        self.correct_button.clicked.connect(self.correct_last_card)
        registration_actions.addWidget(self.correct_button)
        registration_actions.addStretch()
        layout.addLayout(registration_actions)
        self.scope = plain_label(wrap=True)
        layout.addWidget(self.scope)
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
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(False)
        for column, width in enumerate((160, 120, 65, 65, 150, 130, 70)):
            self.table.setColumnWidth(column, width)
        roster_layout.addWidget(self.table)
        self.show_context = QCheckBox("Академический контекст")
        self.show_context.toggled.connect(self.toggle_context)
        roster_layout.addWidget(self.show_context)
        self.toggle_context(False)
        roster_layout.addLayout(
            actions(
                ("Отметить выбранных…", self.manual_block),
                ("Основания…", self.show_evidence),
                ("Сортировка…", lambda: sort_dialog(self, self.model, self.proxy, 7)),
            )
        )
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
        self.uid_label.setMinimumHeight(44)
        self.queue = QListWidget()
        self.queue.setMaximumHeight(60)
        self.queue.setMinimumHeight(34)
        self.queue.currentItemChanged.connect(
            lambda item, previous: self.pin(item) if item else None
        )
        match_layout.addWidget(self.queue)
        self.course = QComboBox()
        self.course.addItem("Сначала выберите курс", None)
        for course in range(1, 7):
            self.course.addItem(f"{course} курс", course)
        match_layout.addWidget(self.course)
        self.course.currentIndexChanged.connect(self.find)
        self.search = QLineEdit()
        self.search.setPlaceholderText("ФИО, книжка, группа · F3")
        match_layout.addWidget(self.search)
        self.matches = QListWidget()
        self.matches.setMinimumHeight(80)
        match_layout.addWidget(self.matches, 1)
        self.confirm_button = QPushButton("Привязать карту · Enter")
        self.confirm_button.clicked.connect(self.confirm_match)
        self.defer_button = QPushButton("Отложить · Esc")
        self.defer_button.clicked.connect(self.defer)
        self.last_card_label = plain_label("Последняя карта: ещё не предъявлена", wrap=True)
        match_layout.addWidget(self.last_card_label)
        self.feedback = plain_label(wrap=True)
        match_layout.addWidget(self.feedback)
        match_scroll = QScrollArea()
        match_scroll.setWidgetResizable(True)
        match_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        match_scroll.setWidget(matching)
        self.match_scroll = match_scroll
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
        column_layout.addWidget(self.uid_label)
        column_layout.addWidget(match_scroll, 1)
        column_layout.addWidget(self.confirm_button)
        column_layout.addWidget(self.defer_button)
        splitter.addWidget(matching_column)
        splitter.setSizes([650, 320])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.blocks.currentIndexChanged.connect(self.show_block)
        self.today.toggled.connect(self.refresh)
        self.search.textChanged.connect(self.find)
        self.cache = StudentSearch(())
        self.next_match = QShortcut(QKeySequence("Down"), self.search)
        self.next_match.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.next_match.activated.connect(self.focus_matches)
        self.matches.itemActivated.connect(lambda *_: self.confirm_match())

    def toggle_context(self, visible):
        for column in (1, 3, 4, 5):
            self.table.setColumnHidden(column, not visible)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and watched in (
            self.queue,
            self.course,
            self.search,
            self.matches,
        ):
            self.reveal_matching_field(watched)
        return super().eventFilter(watched, event)

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
            self.match_scroll.ensureWidgetVisible(field, 0, 8)

    def refresh(self, *_):
        self.cache = StudentSearch(self.api.list_student_contexts())
        selected = self.blocks.currentData()
        self.blocks.blockSignals(True)
        self.blocks.clear()
        blocks = self.api.list_blocks()
        paired = {pair for block in blocks for pair in block.pair_ids}
        disciplines = {d.id: d.name for d in self.service.list_disciplines()}
        rosters = {r.id: r for r in self.api.list_rosters()}
        lessons = self.service.list_lessons()
        by_id = {p.id: p for p in lessons}
        entries = []
        for block in blocks:
            if self.today.isChecked() and block.date != date.today():
                continue
            roster = rosters[block.roster_id]
            entries.append(
                (
                    by_id[block.pair_ids[0]].starts_at,
                    f"{block.date:%d.%m.%Y} · пара {block.start_slot} · {disciplines[roster.discipline_id]} · {block.title or roster.name} · {block.room}",
                    block.id,
                )
            )
        for lesson in lessons:
            if lesson.id in paired:
                continue
            if (
                self.today.isChecked()
                and lesson.starts_at.astimezone(ZoneInfo(lesson.timezone)).date() != date.today()
            ):
                continue
            entries.append(
                (
                    lesson.starts_at,
                    lesson_label(lesson, disciplines[lesson.discipline_id]),
                    f"lesson:{lesson.id}",
                )
            )
        for _, label, key in sorted(entries, key=lambda item: item[0]):
            self.blocks.addItem(label, key)
        index = self.blocks.findData(selected)
        self.blocks.setCurrentIndex(max(0, index))
        self.blocks.blockSignals(False)
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
            for pair in block.pair_ids:
                contexts = self.api.get_pair_contexts(pair)
                self.expected_by_pair[pair] = {s.student_id for s in contexts}
                students.update((s.student_id, s) for s in contexts)
            self.scope.setText(
                f"Просмотр: блок #{block.id} · пары {', '.join(map(str, block.pair_ids))}. Просмотр не меняет активную регистрацию."
            )
        elif self.viewed_lesson_id is not None:
            pair_ids = (self.viewed_lesson_id,)
            expected = self.service.get_lesson_roster(self.viewed_lesson_id)
            self.expected_by_pair[self.viewed_lesson_id] = expected
            students.update(
                (s.student_id, s) for s in self.cache.students if s.student_id in expected
            )
            self.scope.setText(
                "Отдельное занятие: F9 — открыть регистрацию. Просмотр не переключает регистрацию."
            )
        else:
            self.scope.setText(
                "Занятий нет. Снимите «Только сегодня», чтобы увидеть другие даты, или создайте занятие в «Подготовке»."
            )
        if self.course.currentData() is None:
            courses = {
                s.course for s in (students.values() or self.cache.students) if s.course is not None
            }
            if len(courses) == 1:
                self.course.setCurrentIndex(self.course.findData(courses.pop()))
        for pair in pair_ids:
            for evidence in self.api.pair_evidence(pair):
                if (
                    evidence.student_id is None
                    and evidence.uid
                    and evidence.uid not in self.pending_uids
                ):
                    self.pending_uids.append(evidence.uid)
                    self.queue.addItem(evidence.uid)
        if self.pinned_uid is None and self.pending_uids:
            self.pinned_uid = self.pending_uids[0]
            self.uid_label.setText("Сопоставление карты: " + self.pinned_uid)
        self.model.set_students(students.values(), pair_ids)
        for column in range(7, self.model.columnCount()):
            self.table.setColumnWidth(column, 105)
        self.update_counts()
        self.find()

    def viewed_pair_ids(self):
        if self.viewed_block_id is not None:
            return self.api.get_block(self.viewed_block_id).pair_ids
        return (self.viewed_lesson_id,) if self.viewed_lesson_id is not None else ()

    def select_lesson(self, lesson_id):
        block = next((b for b in self.api.list_blocks() if lesson_id in b.pair_ids), None)
        key = block.id if block else f"lesson:{lesson_id}"
        if self.blocks.findData(key) < 0 and self.today.isChecked():
            self.today.setChecked(False)
        self.blocks.setCurrentIndex(self.blocks.findData(key))

    def update_counts(self):
        self.expand_button.setEnabled(self.controller.active_session_id is not None)
        pair_ids = self.viewed_pair_ids()
        if not pair_ids:
            self.counts.setText("Состав: 0")
            return
        text = []
        values = {}
        people = {s.student_id: s for s in self.cache.students}
        outside = {}
        for pair in pair_ids:
            for evidence in self.api.pair_evidence(pair):
                if evidence.student_id and evidence.active:
                    if evidence.student_id in people and evidence.student_id not in {
                        s.student_id for s in self.model.students
                    }:
                        outside[evidence.student_id] = people[evidence.student_id]
                    key = (evidence.student_id, pair)
                    if key not in values or evidence.kind != "manual":
                        values[key] = (
                            "Ручное подтверждение " if evidence.kind == "manual" else "Карта "
                        ) + evidence.at.strftime("%H:%M")
            count = self.service.lesson_counts(pair)
            text.append(
                f"Пара #{pair}: {count.identified_students}/{count.expected_students} · карт без связи: {count.unidentified_cards}"
            )
        if outside:
            self.model.append_students(outside.values())
        self.model.update_attendance(values)
        self.counts.setText(" | ".join(text))

    def selected_ids(self):
        return {i.data(IDENTITY) for i in self.table.selectionModel().selectedRows()}

    def open_selected(self):
        if self.viewed_lesson_id is not None:
            self.open_standalone(self.viewed_lesson_id)
            self.search.setFocus() if self.course.currentData() is not None else self.course.setFocus()
            return
        if self.viewed_block_id is None:
            self.scope.setText("Выберите занятие. Для других дат снимите «Только сегодня».")
            return
        block = self.api.get_block(self.viewed_block_id)
        dialog = FormDialog("Открытие регистрации — охват", self)
        targets = choices([(f"Пара #{p}", p) for p in block.pair_ids], block.pair_ids)
        dialog.form.addRow("Явно выберите пары", targets)
        dialog.finish()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            guarded(
                self,
                lambda: (self.controller.open_session(block.id, checked(targets)), self.changed()),
            )
            if self.controller.active_session_id is not None:
                self.search.setFocus() if self.course.currentData() is not None else self.course.setFocus()

    def _open_standalone(self, lesson_id):
        guarded(self, lambda: (self.controller.open_lesson(lesson_id), self.changed()))

    def close_active(self):
        guarded(self, lambda: (self.controller.close_session(), self.changed()))

    def expand(self):
        guarded(self, lambda: (self.controller.expand_session(), self.changed()))

    def focus_matches(self):
        if self.matches.count():
            self.matches.setCurrentRow(0)
            self.matches.setFocus()

    def find(self, *_):
        if not hasattr(self, "matches"):
            return
        self.matches.clear()
        if self.course.currentData() is None:
            return
        roster_ids = {p.student_id for p in self.model.students}
        for student in sorted(
            [
                s
                for s in self.cache.find(self.search.text())
                if s.course == self.course.currentData()
            ],
            key=lambda s: s.student_id not in roster_ids,
        )[:80]:
            item = QListWidgetItem(
                f"{student.full_name} · {student.record_book_number or 'книжка не указана'} · {student.course} курс · гр. {student.group_number or '—'} · {student.specialty} · {student.profile} · {getattr(student, 'subgroup', '') or '—'}",
                self.matches,
            )
            item.setData(IDENTITY, student.student_id)
        self.matches.setCurrentRow(-1)

    def scan(self, scan, result):
        if result.kind in ("success", "repeat"):
            self.last_card_uid = scan.uid
            self.last_card_name = result.full_name
            self.update_last_card()
        if result.full_name is None and result.kind in ("success", "repeat"):
            if scan.uid not in self.pending_uids:
                self.pending_uids.append(scan.uid)
                self.queue.addItem(scan.uid)
            if self.pinned_uid is None and not self.manual_mode:
                self.pinned_uid = scan.uid
                self.uid_label.setText("Карта: " + scan.uid + " · выберите ФИО ↓ и Enter")
            elif self.pinned_uid != scan.uid and not self.manual_mode:
                self.feedback.setText(
                    f"Карта {scan.uid} сохранена в очереди. Сейчас сопоставляется {self.pinned_uid}."
                )
        self.update_counts()

    def pin(self, item):
        if not self.manual_mode:
            if self.pinned_uid != item.text():
                self.matches.setCurrentRow(-1)
            self.pinned_uid = item.text()
            self.uid_label.setText("Сопоставление карты: " + self.pinned_uid)

    def update_last_card(self):
        self.correct_button.setEnabled(self.last_card_uid is not None)
        self.last_card_label.setText(
            f"Последняя карта: {self.last_card_uid} · {self.last_card_name or 'Не сопоставлена'}"
            if self.last_card_uid
            else "Последняя карта: ещё не предъявлена"
        )
        self.correct_button.setToolTip(self.last_card_label.text())

    def correct_last_card(self):
        if self.last_card_uid:
            self.edit_card(self.last_card_uid)

    def card_binding_changed(self, uid):
        card = next((c for c in self.service.list_cards() if c.uid == uid), None)
        if card is None:
            return
        if uid == self.last_card_uid:
            self.last_card_name = card.full_name
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
        if self.controller.active_lesson_id is None:
            private_message(self, "Ручная отметка", "Сначала откройте регистрацию занятия.")
            return
        if not self.manual_mode:
            self.saved_query = self.search.text()
            self.saved_cursor = self.search.cursorPosition()
            self.manual_session_id = self.controller.active_session_id
            self.manual_lesson_id = self.controller.active_lesson_id
        self.manual_mode = True
        self.uid_label.setText("Ручная отметка — выберите ФИО и нажмите Enter")
        self.confirm_button.setText("Отметить посещение · Enter")
        self.search.clear()
        self.course.setFocus() if self.course.currentData() is None else self.search.setFocus()

    def confirm_match(self):
        item = self.matches.currentItem()
        if item is None:
            self.feedback.setText("Явно выберите студента в результатах поиска.")
            return
        student_id = item.data(IDENTITY)

        def apply():
            if self.manual_mode:
                if self.manual_session_id is not None:
                    self.api.session_manual_mark(
                        self.manual_session_id, student_id, "Оператор подтвердил присутствие"
                    )
                else:
                    if (
                        self.controller.active_session_id is not None
                        or self.controller.active_lesson_id != self.manual_lesson_id
                    ):
                        raise ValueError(
                            "Регистрация изменилась. Нажмите Esc и начните ручную отметку заново: F2."
                        )
                    self.api.create_manual_batch(
                        {(student_id, self.manual_lesson_id)}, "Оператор подтвердил присутствие"
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
                self.service.bind_card(self.pinned_uid, student_id)
                self.last_card_uid = self.pinned_uid
                self.last_card_name = next(
                    (s.full_name for s in self.cache.students if s.student_id == student_id), None
                )
                self.update_last_card()
                self.feedback.setText("Карта сопоставлена; сохранённые предъявления раскрыты.")
                self.defer()
                self.search.clear()
                self.search.setFocus()
            else:
                self.feedback.setText("Сначала откройте регистрацию и приложите карту студента.")
            self.update_counts()
            self.evidence_changed()

        guarded(self, apply)

    def defer(self):
        if self.manual_mode:
            self.manual_mode = False
            self.confirm_button.setText("Привязать карту · Enter")
            self.search.setText(self.saved_query)
            self.search.setCursorPosition(self.saved_cursor)
            self.search.setFocus()
        elif self.pinned_uid:
            self.pending_uids.remove(self.pinned_uid)
            self.queue.clear()
            self.queue.addItems(self.pending_uids)
            self.pinned_uid = self.pending_uids[0] if self.pending_uids else None
            self.matches.setCurrentRow(-1)
        self.uid_label.setText(
            "Сопоставление карты: " + self.pinned_uid
            if self.pinned_uid
            else "Нет несопоставленных карт"
        )

    def manual_block(self):
        ids = self.selected_ids()
        pairs = self.viewed_pair_ids()
        if not ids or not pairs:
            return
        targets = {(student, pair) for student in ids for pair in pairs}
        names = "\n".join(s.full_name for s in self.model.students if s.student_id in ids)
        reason, ok = QInputDialog.getText(
            self,
            "Ручная отметка выбранных",
            f"{self.blocks.currentText()} · пары {pairs}\n{names}\nСтудентов: {len(ids)} · отметок: {len(targets)}\nПричина:",
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
        for student in sorted(ids):
            for pair in pairs:
                for evidence in self.api.evidence(student, pair):
                    item = QListWidgetItem(
                        f"{student} · пара {pair} · {evidence.kind} · {evidence.at:%d.%m %H:%M} · {'действует' if evidence.active else 'отменено'}",
                        listing,
                    )
                    item.setData(IDENTITY, evidence.batch_id)
        dialog.form.addRow(listing)

        def revoke():
            item = listing.currentItem()
            if item and item.data(IDENTITY) is not None:
                self.api.revoke_manual_batch(item.data(IDENTITY))
                self.update_counts()
                self.evidence_changed()
                dialog.accept()

        dialog.form.addRow(
            actions(("Отменить выбранную ручную пачку", lambda: guarded(dialog, revoke)))
        )
        dialog.finish()
        dialog.exec()
