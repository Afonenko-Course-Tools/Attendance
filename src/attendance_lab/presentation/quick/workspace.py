"""GUI-thread coordinator: SCXML control, identity selection and immutable effects."""

import json
from collections.abc import Callable
from uuid import uuid4

import PySide6.QtCore as QtCore
from PySide6.QtCore import SLOT, Property, QObject, Qt, QThread, Signal, Slot
from PySide6.QtQml import QJSEngine, QJSValue
from PySide6.QtScxml import QScxmlEvent
from reactivex import operators as ops
from reactivex.abc import SchedulerBase
from reactivex.scheduler.mainloop import QtScheduler
from reactivex.subject import Subject

from attendance_lab.application.course_commands import journal_mark_availability
from attendance_lab.application.list_queries import StudentSearch, filter_report_rows
from attendance_lab.application.operations import Operation
from attendance_lab.application.quick_backend import QuickBackend, ViewContext
from attendance_lab.application.text_search import normalized
from attendance_lab.presentation.quick.academic_models import JournalTableModel
from attendance_lab.presentation.quick.charts import load_chart
from attendance_lab.presentation.quick.editing_state import CardMatchDraft, CourseEditingState
from attendance_lab.presentation.quick.models import RecordListModel
from attendance_lab.presentation.quick.workspace_worker import Request, Response, WorkspaceWorker

READ_COMMANDS = frozenset(
    {
        "operation.resolve",
        "view.refresh",
        "registration.search",
        "grading.topic_search",
        "grading.preview",
        "grading.report_preview",
        "grading.fit",
        "grading.load_template",
        "journal.filter",
        "journal.history",
        "students.filter",
        "students.history",
        "students.preview_import",
        "system.readers",
        "system.archive",
    }
)
DEBOUNCED = frozenset(
    {
        "registration.search",
        "grading.topic_search",
        "grading.preview",
        "grading.report_preview",
        "journal.filter",
        "students.filter",
    }
)
COLLECTIONS = (
    "courses",
    "topics",
    "rosters",
    "lessons",
    "journalRows",
    "students",
    "registrationRows",
    "unknownCards",
    "matchingStudents",
    "logRows",
    "gradingReportRows",
    "gradingWorkRows",
    "gradingChangeRows",
    "systemArchiveRows",
    "systemArchiveLogRows",
    "systemStudentRows",
)


class Workspace(QObject):
    changed = Signal()
    stateChanged = Signal()
    contextChanged = Signal()
    draftsChanged = Signal()
    commandSucceeded = Signal(str, dict)
    commandFailed = Signal(str, str)
    publicResult = Signal(object)
    shutdownReady = Signal()
    request = Signal(object)
    shutdownRequested = Signal()

    def __init__(
        self,
        factory: Callable[[], QuickBackend],
        parent=None,
        *,
        search_scheduler: SchedulerBase | None = None,
    ):
        super().__init__(parent)
        self._state = {}
        self._qml_engine: QJSEngine | None = None
        self._qml_state = QJSValue()
        self._course = self._lesson = None
        self._revision = 0
        self._pending: dict[str, Request] = {}
        self._read_versions: dict[str, int] = {}
        self._failed: Request | None = None
        self._recovery_target: Request | None = None
        self._error = ""
        self._refresh_error = ""
        self._ready = False
        self._disposed = False
        self._shutdown_notified = False
        self._editing = CourseEditingState()
        self._matching = CardMatchDraft()
        self._column_settings = (
            QtCore.QSettings() if QtCore.QCoreApplication.organizationName() else None
        )
        stored_columns = (
            self._column_settings.value("tables/quick-reports", {})
            if self._column_settings
            else None
        )
        self._report_columns = stored_columns if isinstance(stored_columns, dict) else {}
        self._subjects = {}
        self._subscriptions = []
        self._search_scheduler = search_scheduler or QtScheduler(QtCore)
        self._effect_requests: dict[str, Request] = {}
        self._models = {name: RecordListModel(self) for name in COLLECTIONS}
        self._journal = JournalTableModel(self)
        self.machine = load_chart(self, "workspace.scxml")
        self.machine.reachedStableState.connect(self.changed)
        self.machine.reachedStableState.connect(self._notify_shutdown)
        self._effect_connection = self.machine.connectToEvent(
            "effect.*",
            self,
            SLOT("dispatch(QScxmlEvent)"),  # type: ignore[arg-type]
        )
        self.worker_thread = QThread(self)
        self.worker_thread.setObjectName("attendance-storage-owner")
        self.worker = WorkspaceWorker(factory)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.initialize)
        self.request.connect(self.worker.execute, Qt.ConnectionType.QueuedConnection)
        self.shutdownRequested.connect(self.worker.shutdown, Qt.ConnectionType.QueuedConnection)
        self.worker.completed.connect(self.accept_response, Qt.ConnectionType.QueuedConnection)
        self.worker.stopped.connect(self._stopped, Qt.ConnectionType.QueuedConnection)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.machine.start()
        self.worker_thread.start()

    @Property(dict, notify=stateChanged)
    def state(self):
        return self._state

    @Property(QJSValue, notify=stateChanged)
    def qmlState(self):
        return self._qml_state

    def bind_qml_engine(self, engine: QJSEngine) -> None:
        """Convert each snapshot once, using only its private GUI engine.

        A QVariantMap getter otherwise deeply converts the full snapshot on
        every field access from a delegate. Keep the Python state API separate.
        """
        assert QThread.currentThread() == self.thread() == engine.thread()
        if self._qml_engine is not None and self._qml_engine is not engine:
            raise RuntimeError("A workspace belongs to one private QML engine")
        # The composition root drains and deletes this owner. A script wrapper
        # must not let JavaScript GC destroy its QThread and live subscriptions.
        QJSEngine.setObjectOwnership(self, QJSEngine.ObjectOwnership.CppOwnership)
        self._qml_engine = engine
        self._qml_state = engine.toScriptValue(self._state)
        self.stateChanged.emit()

    @Property(int, notify=contextChanged)
    def selectedCourseId(self):
        return self._course or 0

    @Property(int, notify=contextChanged)
    def selectedLessonId(self):
        return self._lesson or 0

    @Property(bool, notify=changed)
    def ready(self):
        return self._ready

    @Property(bool, notify=changed)
    def busy(self):
        return (
            bool(self._pending or self._effect_requests)
            or not self._ready
            or self.machine.isActive("draining")
            or self.machine.isActive("stopping")
        )

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(str, notify=changed)
    def refreshError(self):
        return self._refresh_error

    @Property(bool, notify=changed)
    def canRetry(self):
        return self._failed is not None and not self._pending

    @Property(bool, notify=changed)
    def canOpen(self):
        return (
            bool(self._lesson)
            and not self.busy
            and self._failed is None
            and any(self.machine.isActive(s) for s in ("ready", "openFailed"))
        )

    @Property(bool, notify=changed)
    def canClose(self):
        return (
            not self.busy
            and (self._failed is None or self._failed.operation.kind == "registration.close")
            and any(self.machine.isActive(s) for s in ("open", "closeFailed"))
        )

    @Property(bool, notify=changed)
    def canOpenLegacy(self):
        return (
            not self.busy
            and self._failed is None
            and bool(self._state.get("system", {}).get("archive_lesson_id"))
            and any(self.machine.isActive(s) for s in ("noLesson", "ready", "openFailed"))
        )

    @Property(bool, notify=changed)
    def canSaveMatch(self):
        return (
            not self.busy
            and self._failed is None
            and self.machine.isActive("matchDraft")
            and bool(self._matching.get("uid"))
            and bool(self._matching.get("student_id"))
        )

    @Property(bool, notify=changed)
    def canEditMatch(self):
        return (
            "match" not in self._effect_requests
            and not self.machine.isActive("matchSaving")
            and not (
                self._failed is not None and self._failed.operation.kind == "registration.bind"
            )
        )

    @Property(bool, notify=changed)
    def canManual(self):
        selected = self.current_drafts().get("registration", {}).get("student_id")
        return (
            bool(self._lesson)
            and not self.busy
            and self._failed is None
            and any(
                row["id"] == selected and not row["present"]
                for row in self._models["registrationRows"].rows
            )
        )

    @Property(bool, notify=changed)
    def canMarkSelected(self):
        if self.busy or self._failed is not None:
            return False
        present = {
            (row["id"], cell["topic_id"])
            for row in self._state.get("journal", {}).get("rows", [])
            for cell in row.get("cells", [])
            if cell.get("present")
        }
        available = journal_mark_availability(
            ((cell["student_id"], cell["topic_id"]) for cell in self._editing.selected_cells),
            (lesson["topic_id"] for lesson in self._state.get("lessons", [])),
            present,
        )
        return available.enabled

    @Property(bool, notify=changed)
    def shutdownComplete(self):
        return self.machine.isActive("terminated")

    @Property(dict, notify=draftsChanged)
    def drafts(self):
        return self.current_drafts()

    def current_drafts(self) -> dict:
        drafts = self._editing.drafts
        drafts["matching"] = self._matching.snapshot()
        return drafts

    @Property(list, notify=changed)
    def selectedCells(self):
        return self._editing.selected_cells

    @Property(list, notify=changed)
    def selectedStudents(self):
        return self._editing.selected_students

    courses = Property(QObject, lambda self: self._models["courses"], constant=True)
    topics = Property(QObject, lambda self: self._models["topics"], constant=True)
    rosters = Property(QObject, lambda self: self._models["rosters"], constant=True)
    lessons = Property(QObject, lambda self: self._models["lessons"], constant=True)
    journalRows = Property(QObject, lambda self: self._models["journalRows"], constant=True)
    students = Property(QObject, lambda self: self._models["students"], constant=True)
    registrationRows = Property(
        QObject, lambda self: self._models["registrationRows"], constant=True
    )
    unknownCards = Property(QObject, lambda self: self._models["unknownCards"], constant=True)
    matchingStudents = Property(
        QObject, lambda self: self._models["matchingStudents"], constant=True
    )
    logRows = Property(QObject, lambda self: self._models["logRows"], constant=True)
    gradingReportRows = Property(
        QObject, lambda self: self._models["gradingReportRows"], constant=True
    )
    gradingWorkRows = Property(QObject, lambda self: self._models["gradingWorkRows"], constant=True)
    gradingChangeRows = Property(
        QObject, lambda self: self._models["gradingChangeRows"], constant=True
    )
    journalTable = Property(QObject, lambda self: self._journal, constant=True)
    systemArchiveRows = Property(
        QObject, lambda self: self._models["systemArchiveRows"], constant=True
    )
    systemArchiveLogRows = Property(
        QObject, lambda self: self._models["systemArchiveLogRows"], constant=True
    )
    systemStudentRows = Property(
        QObject, lambda self: self._models["systemStudentRows"], constant=True
    )

    def snapshot(self) -> dict:
        return self._state

    def _context(self):
        return ViewContext(self._course, self._lesson, self._revision)

    @Slot(int)
    def selectCourse(self, course_id):
        value = course_id if course_id > 0 else None
        if self._course != value:
            self._course, self._lesson = value, None
            self._editing.switch_course(value)
            self._revision += 1
            self.contextChanged.emit()
            self.draftsChanged.emit()
            self.changed.emit()
            self.command("view.refresh", {})

    @Slot(int)
    def selectLesson(self, lesson_id):
        self._lesson = lesson_id if lesson_id > 0 else None
        self._revision += 1
        self.machine.submitEvent("lesson.selected" if self._lesson else "lesson.cleared")
        self.contextChanged.emit()
        self.changed.emit()
        self.command("view.refresh", {})

    @Slot(str, str, "QVariant")
    def setDraft(self, scope, key, value):
        value = value.toVariant() if isinstance(value, QJSValue) else value
        if scope == "matching":
            if not self.canEditMatch:
                return
            self._matching.set(key, value)
        else:
            self._editing.set_draft(scope, key, value)
            if scope == "registration" and key in ("sort", "descending", "roster_query"):
                self._update_registration_rows()
            elif scope == "grading" and key.partition(":")[2] in (
                "search",
                "report_group",
                "report_status",
                "report_lesson",
                "report_sort",
                "report_descending",
                "work_sort",
            ):
                self._update_grading_rows()
        self.draftsChanged.emit()
        self.changed.emit()

    @Slot(str, bool)
    def pickCard(self, uid, correction=False):
        card = next(
            (c for c in self._state.get("registration", {}).get("cards", []) if c["uid"] == uid),
            None,
        )
        if card is None or not self.canEditMatch:
            return
        self._matching.pick(card, correction)
        self.machine.submitEvent("match.edit")
        self.draftsChanged.emit()
        self.changed.emit()

    @Slot()
    def deferMatch(self):
        if self.canEditMatch:
            self._matching.clear()
            self.machine.submitEvent("intent.defer")
            self.draftsChanged.emit()
            self.changed.emit()

    @Slot()
    def correctLastCard(self):
        last = self._state.get("registration", {}).get("last_scan")
        if last:
            self.pickCard(last["uid"], True)

    @Slot()
    def manualSelected(self):
        if self.canManual:
            self._prepare_manual()

    @Slot()
    def toggleManual(self):
        self._prepare_manual(toggle=True)

    @Slot()
    def clearManual(self):
        self._prepare_manual(revoke_only=True)

    def _prepare_manual(self, *, toggle=False, revoke_only=False):
        if not self._lesson or self.busy or self._failed is not None:
            return
        lesson = next(
            (item for item in self._state.get("lessons", []) if item["id"] == self._lesson), None
        )
        draft = self.current_drafts().get("registration", {})
        student_id = draft.get("student_id")
        if not lesson or not any(
            row["id"] == student_id for row in self._models["registrationRows"].rows
        ):
            return
        self.command(
            "journal.prepare_mark",
            {
                "course_id": self._course,
                "cells": [{"student_id": student_id, "topic_id": lesson["topic_id"]}],
                "reason": "был без пропуска" if toggle else draft.get("reason", "был без пропуска"),
                "occurrence_id": lesson["id"],
                "toggle": toggle,
                "revoke_only": revoke_only,
            },
        )

    @Slot(str, int, bool)
    def selectCell(self, student_id, topic_id, extend=False):
        self._editing.select_cell(
            student_id,
            topic_id,
            [row["id"] for row in self._journal.rows],
            [topic["id"] for topic in self._journal.topics],
            extend,
        )
        self.changed.emit()

    @Slot(str, bool)
    def selectStudent(self, student_id, extend=False):
        ids = [str(s.get("id", s.get("student_id", ""))) for s in self._state.get("students", [])]
        self._editing.select_student(student_id, ids, extend)
        self.changed.emit()

    @Slot(str, bool, list)
    def selectStudentInOrder(self, student_id, extend, ids):
        self._editing.select_student(student_id, ids, extend)
        self.changed.emit()

    @Slot(str, "QVariantMap")
    def command(self, name, payload):
        if (
            self._disposed
            or self.machine.isActive("draining")
            or self.machine.isActive("stopping")
            or self.machine.isActive("terminated")
        ):
            return
        if (
            name == "registration.close"
            and self._failed is not None
            and self._failed.operation.kind == name
        ):
            self.retry()
            return
        payload = payload.toVariant() if isinstance(payload, QJSValue) else dict(payload)
        payload.setdefault("course_id", self._course)
        payload.setdefault("lesson_id", self._lesson)
        if name == "registration.close":
            current = self._state.get("registration", {})
            payload = {
                "session_id": current.get("active_session_id"),
                "lesson_id": current.get("active_lesson_id"),
            }
        elif name == "registration.bind":
            payload = self._matching.snapshot()
            payload.pop("owner_name", None)
        if (
            name not in READ_COMMANDS
            and name != "registration.retry"
            and (self._pending or self._effect_requests or self._failed is not None)
        ):
            self._error = "Дождитесь результата или повторите неподтверждённую операцию."
            self.changed.emit()
            return
        if (
            name == "registration.open"
            and not self.canOpen
            or name == "registration.open_legacy"
            and not self.canOpenLegacy
            or name == "registration.close"
            and not self.canClose
            or name == "registration.bind"
            and not self.canSaveMatch
        ):
            return
        generation = self._read_versions.get(name, 0) + 1 if name in READ_COMMANDS else 0
        if generation:
            self._read_versions[name] = generation
        request = Request(
            Operation.create(str(uuid4()), name, payload), self._context(), generation
        )
        effect = self._effect_for(name)
        if effect is not None:
            self._effect_requests[effect] = request
            self.machine.submitEvent("intent." + effect)
            self.changed.emit()
        elif name in DEBOUNCED:
            if name not in self._subjects:
                subject = Subject()
                self._subjects[name] = subject
                self._subscriptions.append(
                    subject.pipe(ops.debounce(0.1, scheduler=self._search_scheduler)).subscribe(
                        self._send
                    )
                )
            self._subjects[name].on_next(request)
        else:
            self._send(request)

    @Slot(QScxmlEvent)
    def dispatch(self, event):
        effect = event.name().removeprefix("effect.")
        if effect == "shutdown":
            self.shutdownRequested.emit()
        elif effect == "drain":
            self._check_drained()
        else:
            request = self._effect_requests.pop(effect, None)
            if request is not None:
                self._send(request)

    def _send(self, request):
        if self._disposed:
            return
        if request.operation.kind in READ_COMMANDS and any(
            self.machine.isActive(s) for s in ("draining", "stopping", "terminated")
        ):
            return
        self._pending[request.operation.operation_id] = request
        self._error = ""
        self.request.emit(request)
        self.changed.emit()

    def _check_drained(self):
        if self.machine.isActive("draining") and not self._pending and not self._effect_requests:
            self.machine.submitEvent("operations.drained")

    @Slot()
    def retry(self):
        if self._failed is None or self._pending:
            return
        if self._failed.operation.kind == "registration.close" and self._state.get(
            "registration", {}
        ).get("blocked"):
            self._recovery_target = self._failed
            self.command("registration.retry", {})
            return
        request, self._failed = self._failed, None
        kind = request.operation.kind
        effect = self._effect_for(kind)
        if effect:
            self._effect_requests[effect] = request
            self.machine.submitEvent("intent." + effect)
            self.changed.emit()
        else:
            self._send(request)

    @Slot()
    def resolveFailed(self):
        if self._failed is not None and not self._pending:
            self.command("operation.resolve", {"operation_id": self._failed.operation.operation_id})

    @Slot(object)
    def accept_response(self, response: Response):
        assert QThread.currentThread() == self.thread()
        if self._disposed:
            return
        for result in response.public_results:
            self.publicResult.emit(result)
        request = response.request
        succeeded = None
        if request:
            self._pending.pop(request.operation.operation_id, None)
            name = request.operation.kind
            if request.read_generation and (
                request.context.revision != self._revision
                or request.read_generation != self._read_versions.get(name)
            ):
                self.changed.emit()
                self._check_drained()
                return
            effect = self._effect_for(name)
            if effect:
                self.machine.submitEvent(f"result.{effect}.{'error' if response.error else 'ok'}")
            if response.error:
                self._error = response.error
                if name not in READ_COMMANDS and not (
                    name == "registration.retry" and self._recovery_target is not None
                ):
                    self._failed = request
                self.commandFailed.emit(name, response.error)
            else:
                result = json.loads(response.result_json)
                result["_context_course_id"] = request.context.course_id
                if name == "operation.resolve" and self._failed is not None:
                    if result.get("committed"):
                        self.retry()
                    else:
                        self._failed = None
                        self._error = (
                            "Операция не была сохранена. Можно изменить ввод и подтвердить заново."
                        )
                if effect == "match":
                    self._matching.clear()
                    self.draftsChanged.emit()
                if request.context.revision == self._revision:
                    succeeded = (name, result)
        else:
            if response.error:
                self._error = response.error
            else:
                self._ready = True
        self._refresh_error = response.refresh_error
        current_read = (
            request is None
            or not request.read_generation
            or request.read_generation == self._read_versions.get(request.operation.kind)
        )
        if (
            response.snapshot_json != "{}"
            and response.context.revision == self._revision
            and current_read
        ):
            self._apply_snapshot(json.loads(response.snapshot_json))
        elif request and response.context.revision != self._revision:
            self.command("view.refresh", {})
        if succeeded is not None:
            self.commandSucceeded.emit(*succeeded)
            result = succeeded[1]
            if result.get("select_course_id"):
                self.selectCourse(int(result["select_course_id"]))
            if result.get("select_lesson_id"):
                self.selectLesson(int(result["select_lesson_id"]))
        if (
            request
            and request.operation.kind == "registration.retry"
            and self._recovery_target is not None
        ):
            self._recovery_target = None
            if not response.error:
                self.retry()
        self.changed.emit()
        self._check_drained()

    @staticmethod
    def _effect_for(name):
        effects = {
            "registration.open": "open",
            "registration.open_legacy": "open",
            "registration.close": "close",
            "registration.bind": "match",
        }
        if name in effects:
            return effects[name]
        return "command" if name not in READ_COMMANDS else None

    def _apply_snapshot(self, state):
        state_changed = self._state != state
        previous_context = (self._course, self._lesson)
        previous_scan = self._state.get("registration", {}).get("last_scan")
        self._state = state
        self._course = state.get("selected_course_id")
        self._editing.switch_course(self._course)
        self._lesson = state.get("selected_lesson_id")
        if self._lesson and self.machine.isActive("noLesson"):
            self.machine.submitEvent("lesson.selected")
        registration, grading = state.get("registration", {}), state.get("grading", {})
        scan = registration.get("last_scan")
        if scan and scan != previous_scan and not self._matching.get("uid") and self.canEditMatch:
            card = next(
                (card for card in registration.get("unknown", []) if card["uid"] == scan["uid"]),
                None,
            )
            if card is not None:
                self.pickCard(card["uid"])
        journal = state.get("journal", {})
        collections = {
            name: state.get(name, [])
            for name in ("courses", "topics", "rosters", "lessons", "students")
        }
        collections.update(
            journalRows=journal.get("rows", []),
            registrationRows=registration.get("rows", []),
            unknownCards=registration.get("unknown", []),
            matchingStudents=registration.get("students", []),
            logRows=registration.get("logs", []),
            gradingChangeRows=grading.get("changes", []),
        )
        system = state.get("system", {})
        collections.update(
            systemArchiveRows=system.get("archive", []),
            systemArchiveLogRows=system.get("archive_log", []),
            systemStudentRows=system.get("archive_students", []),
        )
        for name, rows in collections.items():
            self._models[name].set_rows(rows)
        self._update_registration_rows()
        self._update_grading_rows()
        self._journal.set_snapshot(journal)
        visible_students = [
            str(row.get("id", row.get("student_id", ""))) for row in state.get("students", [])
        ]
        self._editing.reconcile(
            visible_students,
            [row.get("student_id", row.get("id")) for row in journal.get("rows", [])],
            [topic["id"] for topic in journal.get("topics", [])],
        )
        if previous_context != (self._course, self._lesson):
            self.contextChanged.emit()
            if previous_context[0] != self._course:
                self.draftsChanged.emit()
        if state_changed:
            if self._qml_engine is not None:
                self._qml_state = self._qml_engine.toScriptValue(state)
            self.stateChanged.emit()

    def _update_registration_rows(self):
        rows = self._state.get("registration", {}).get("rows", [])
        draft = self.current_drafts().get("registration", {})
        query = draft.get("roster_query", "")
        if query:
            rows = StudentSearch(rows).find(query)
        key = draft.get("sort", "")
        descending = bool(draft.get("descending"))
        keys = {
            "name": lambda row: row["name"].casefold(),
            "group": self._group_number,
            "present": lambda row: not row["present"],
        }
        if key in keys:
            rows = sorted(rows, key=keys[key], reverse=descending)
        if draft.get("student_id") and not any(row["id"] == draft["student_id"] for row in rows):
            self._editing.set_draft("registration", "student_id", "")
        self._models["registrationRows"].set_rows(rows)

    @staticmethod
    def _group_number(row):
        group = str(row.get("group") or "")
        return int(group) if group.isdecimal() else 0

    @Property(dict, notify=draftsChanged)
    def reportColumns(self):
        return dict(self._report_columns)

    @Slot(str, int)
    def resizeReportColumn(self, field, width):
        if field not in ("full_name", "group", "topic", "lesson", "grade"):
            return
        if width:
            self._report_columns[field] = width
        else:
            self._report_columns.pop(field, None)
        if self._column_settings:
            self._column_settings.setValue("tables/quick-reports", self._report_columns)
        self.draftsChanged.emit()

    @Property(list, notify=changed)
    def visibleReportIds(self):
        return [row["student_id"] for row in self._models["gradingReportRows"].rows]

    @Property(list, notify=changed)
    def visibleGradeStudentIds(self):
        return [row["student_id"] for row in self._models["gradingWorkRows"].rows]

    def _update_grading_rows(self):
        grading = self._state.get("grading", {})
        rows = grading.get("reports", [])
        drafts = self.current_drafts().get("grading", {})
        prefix = f"{self._course}:"
        query = drafts.get(prefix + "search", "")
        searched = StudentSearch(rows).find(query) if query.strip() else rows
        matching = {row["student_id"] for row in searched}
        written = [row for row in grading.get("scoreRows", []) if row["student_id"] in matching]
        sort = drafts.get(prefix + "work_sort") or {}
        field, descending = sort.get("field"), bool(sort.get("descending"))
        if field == "full_name":
            written.sort(key=lambda row: normalized(row["full_name"]), reverse=descending)
        elif isinstance(field, int):
            known = []
            missing = []
            for row in written:
                score = next(
                    (cell["value"] for cell in row["cells"] if cell["work_id"] == field), None
                )
                if score is None:
                    missing.append(row)
                else:
                    known.append((score, row))
            written = [
                row for _, row in sorted(known, key=lambda pair: pair[0], reverse=descending)
            ] + missing
        self._models["gradingWorkRows"].set_rows(written)
        rows = filter_report_rows(
            searched,
            group_id=drafts.get(prefix + "report_group") or None,
            status=drafts.get(prefix + "report_status") or "",
            lesson_id=drafts.get(prefix + "report_lesson") or None,
            sort=drafts.get(prefix + "report_sort") or "",
            descending=bool(drafts.get(prefix + "report_descending")),
        )
        self._models["gradingReportRows"].set_rows(rows)

    @Slot()
    def requestClose(self):
        if self.machine.isActive("running"):
            self.machine.submitEvent("intent.shutdown")

    @Slot(bool, str)
    def _stopped(self, success, error):
        if success:
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.machine.submitEvent("result.shutdown.ok")
        else:
            self._error = error
            self.machine.submitEvent("result.shutdown.error")
        self.changed.emit()

    @Slot()
    def _notify_shutdown(self):
        # submitEvent is asynchronous: windows may close only after the chart
        # has accepted shutdown, otherwise onClosing vetoes app.quit forever.
        if self.machine.isActive("terminated") and not self._shutdown_notified:
            self._shutdown_notified = True
            self.shutdownReady.emit()

    def dispose(self):
        """Call after successful asynchronous shutdown, before deleting the owner."""
        if self._disposed:
            return
        if self.worker_thread.isRunning():
            raise RuntimeError("Drain and close the storage owner before deleting the screen")
        self._disposed = True
        self._qml_state = QJSValue()
        self._qml_engine = None
        for subscription in self._subscriptions:
            subscription.dispose()
        for subject in self._subjects.values():
            subject.dispose()
        self.machine.reachedStableState.disconnect(self.changed)
        self.machine.reachedStableState.disconnect(self._notify_shutdown)
        QObject.disconnect(self._effect_connection)
        self.machine.stop()
