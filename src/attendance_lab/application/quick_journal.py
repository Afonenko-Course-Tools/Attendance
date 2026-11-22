"""Journal projections, selection confirmation and manual evidence history."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from attendance_lab.application.course_absences import course_absences, journal_absences
from attendance_lab.application.journal import (
    DEFAULT_REASON,
    JOURNAL_REASONS,
    TOGGLE_MESSAGES,
    JournalMarkPreparation,
    JournalMarkRequest,
    JournalOccurrenceChoice,
    JournalTopicChoice,
    JournalTopicTarget,
    confirm_journal_mark,
    prepare_journal_mark,
)
from attendance_lab.application.quick_academic_common import (
    SearchIndex,
    course_for,
    integer,
    json_safe,
    local_path,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import EXCUSED_ABSENCE_REASON

MUTATING_COMMANDS = frozenset(["journal.confirm_mark", "journal.prepare_mark", "journal.revoke"])


class JournalActions:
    def __init__(self, service, files=None, timezone="Europe/Minsk"):
        self.service, self.files, self.timezone = service, files, timezone
        self.filters = {}
        self._mark_plans = {}
        self._pending_mark = None
        self._history = []
        self._history_course_id = None
        self.search = SearchIndex()

    def snapshot(self, course_id):
        course = self.service.courses.get_course(course_id) if course_id is not None else None
        topics = self.service.courses.list_topics(course.id) if course else []
        journal = (
            self._journal(course, topics)
            if course
            else {"rows": [], "topics": [], "observed_only": False, "incomplete": False}
        )
        pending = self._pending_mark
        if pending and pending["course_id"] != course_id:
            pending = None
        return {
            "journal": journal,
            "mark_preparation": pending,
            "manual_history": self._history if self._history_course_id == course_id else [],
            "manual_reasons": list(JOURNAL_REASONS),
            "excused_reason": EXCUSED_ABSENCE_REASON,
        }

    def execute(self, name, payload):
        handlers = {
            "journal.filter": "_filter",
            "journal.prepare_mark": "_prepare_mark",
            "journal.confirm_mark": "_confirm_mark",
            "journal.cancel_mark": "_cancel_mark",
            "journal.history": "_manual_history",
            "journal.revoke": "_revoke",
            "journal.export": "_export_journal",
        }
        if name not in handlers:
            raise AttendanceError("Неизвестная команда раздела.")
        return json_safe(getattr(self, handlers[name])(payload) or {})

    def restore_preparation(self, result):
        """Restore only a trusted prepare result returned by the receipt executor.

        Choice preparation shares a command with the automatic single-occurrence
        write. Its receipt must therefore contain enough data for replay after a
        restart; the confirmation still rechecks every live identity and cell.
        This is deliberately not a user-dispatchable command.
        """
        if not result.get("requires_choice"):
            return
        data = result["preparation"]
        prepared = JournalMarkPreparation(
            data["course_id"],
            data["course_name"],
            frozenset((sid, topic) for sid, topic in data["cells"]),
            tuple(
                JournalTopicChoice(
                    topic["topic_id"],
                    topic["number"],
                    topic["title"],
                    tuple(topic["student_names"]),
                    tuple(
                        JournalOccurrenceChoice(
                            item["occurrence_id"],
                            item["block_id"],
                            item["roster_id"],
                            item["roster_name"],
                            datetime.fromisoformat(item["starts_at"]),
                            datetime.fromisoformat(item["ends_at"]),
                            tuple(
                                (batch, student, pair)
                                for batch, student, pair in item.get("revocations", [])
                            ),
                        )
                        for item in topic["occurrences"]
                    ),
                )
                for topic in data["topics"]
            ),
            data["reason"],
            data.get("mode", "mark"),
            data.get("occurrence_id"),
        )
        if data["token"] not in self._mark_plans and len(self._mark_plans) >= 32:
            self._mark_plans.pop(next(iter(self._mark_plans)))
        self._mark_plans[data["token"]] = prepared
        self._pending_mark = dict(data)

    def _journal(self, course, topics):
        api = self.service.courses
        expected_students = api.list_students(course.id)
        observed_only = not expected_students
        students = expected_students or api.list_observed_students(course.id)
        present, excused = api.attendance_statuses(course.id)
        summary = journal_absences(self.service, course.id, present, excused=excused)
        expected = course_absences(self.service, course.id, present, excused=excused)
        filters = self.filters.get(
            course.id,
            {"query": "", "status": "all", "threshold": 1, "sort": "name", "descending": False},
        )
        rows = []
        for student in students:
            missed = summary.missed_topics.get(student.student_id, frozenset())
            unresolved = summary.unresolved_topics.get(student.student_id, frozenset())
            row = dict(
                json_safe(student),
                id=student.student_id,
                name=student.full_name,
                missed_topics=sorted(missed),
                expected_topics=sorted(
                    expected.expected_topics.get(student.student_id, frozenset())
                ),
                unresolved_topics=sorted(unresolved),
                missed_count=len(missed),
                unresolved_count=len(unresolved),
                absence_text="—"
                if observed_only or unresolved and not missed
                else f"≥{len(missed)}"
                if unresolved
                else str(len(missed)),
                cells=[
                    {
                        "student_id": student.student_id,
                        "topic_id": topic.id,
                        "present": (student.student_id, topic.id) in present,
                        "excused": (student.student_id, topic.id) in excused,
                        "missed": topic.id in missed,
                        "unresolved": topic.id in unresolved,
                    }
                    for topic in topics
                ],
            )
            rows.append(row)
        rows = self.search.find(rows, filters["query"])
        if filters["status"] == "missing":
            rows = (
                []
                if observed_only
                else [row for row in rows if row["missed_count"] >= filters["threshold"]]
            )
        if filters["sort"] in ("group_number", "missed_count", "record_book_number"):
            key = filters["sort"]
            rows.sort(
                key=lambda row: (
                    row.get(key) is None,
                    row.get(key) or 0 if key != "record_book_number" else row.get(key, ""),
                    row["name"],
                ),
                reverse=filters["descending"],
            )
        elif filters["descending"]:
            rows.reverse()
        return {
            "rows": rows,
            "topics": [
                dict(json_safe(topic), name=f"{topic.number}. {topic.title}") for topic in topics
            ],
            "observed_only": observed_only,
            "incomplete": summary.incomplete,
            "unresolved_lesson_ids": list(expected.unresolved_lesson_ids),
            **filters,
        }

    def _filter(self, payload):
        course = course_for(self.service, payload)
        current = dict(
            self.filters.get(
                course.id,
                {"query": "", "status": "all", "threshold": 1, "sort": "name", "descending": False},
            )
        )
        for key in ("query", "status", "sort"):
            if key in payload:
                current[key] = str(payload[key])
        if current["status"] not in ("all", "missing") or current["sort"] not in (
            "name",
            "group_number",
            "missed_count",
            "record_book_number",
        ):
            raise AttendanceError("Неизвестный фильтр журнала.")
        if "threshold" in payload:
            current["threshold"] = max(1, integer(payload, "threshold"))
        if "descending" in payload:
            current["descending"] = bool(payload["descending"])
        self.filters[course.id] = current
        return {}

    @staticmethod
    def _cells(payload):
        cells = payload.get("cells", [])
        try:
            return frozenset((str(cell["student_id"]), integer(cell, "topic_id")) for cell in cells)
        except (TypeError, KeyError) as exc:
            raise AttendanceError("Выберите ячейки журнала.") from exc

    def _prepare_mark(self, payload):
        course = course_for(self.service, payload)
        cells = self._cells(payload)
        if not cells:
            raise AttendanceError("Выберите ячейки журнала.")
        toggle = payload.get("toggle") is True
        revoke_only = payload.get("revoke_only") is True
        occurrence = (
            integer(payload, "occurrence_id") if payload.get("occurrence_id") is not None else None
        )
        prepared = prepare_journal_mark(
            self.service,
            JournalMarkRequest(
                course.id,
                cells,
                str(payload.get("reason", DEFAULT_REASON)),
                toggle,
                occurrence if toggle or revoke_only else None,
                revoke_only=revoke_only,
            ),
        )
        if not prepared.cells:
            self._pending_mark = None
            return self._mark_result(False, prepared.mode, feedback=toggle or revoke_only)
        if occurrence is not None or not prepared.requires_choice:
            targets = (
                tuple(JournalTopicTarget(topic.topic_id, occurrence) for topic in prepared.topics)
                if occurrence is not None
                else prepared.automatic_targets
            )
            result = confirm_journal_mark(self.service, prepared, targets)
            self._pending_mark = None
            return self._mark_result(result.changed, result.mode, feedback=toggle or revoke_only)
        token = str(uuid4())
        if len(self._mark_plans) >= 32:
            self._mark_plans.pop(next(iter(self._mark_plans)))
        self._mark_plans[token] = prepared
        self._pending_mark = dict(
            json_safe(prepared),
            token=token,
            automatic_targets=json_safe(prepared.automatic_targets),
            requires_choice=True,
        )
        return {"token": token, "requires_choice": True, "preparation": self._pending_mark}

    def _confirm_mark(self, payload):
        token = str(payload.get("token", ""))
        prepared = self._mark_plans.get(token)
        if prepared is None:
            raise AttendanceError("Выбор занятия устарел. Повторите подготовку отметки.")
        if course_for(self.service, payload).id != prepared.course_id:
            raise AttendanceError("Подготовленная отметка относится к другому курсу.")
        targets = tuple(
            JournalTopicTarget(integer(target, "topic_id"), integer(target, "occurrence_id"))
            for target in payload.get("targets", [])
        )
        result = confirm_journal_mark(
            self.service, prepared, targets, reason=str(payload.get("reason", prepared.reason))
        )
        self._pending_mark = None
        return self._mark_result(result.changed, result.mode, feedback=prepared.mode == "revoke")

    @staticmethod
    def _mark_result(changed, mode, *, feedback):
        if not feedback:
            return {"changed": changed}
        message = TOGGLE_MESSAGES.get(mode, "") if changed or mode in ("card", "empty") else ""
        return {"changed": changed, "mode": mode, "message": message}

    def _cancel_mark(self, payload):
        token = str(payload.get("token", ""))
        self._mark_plans.pop(token, None)
        self._pending_mark = None
        return {}

    def _manual_history_scope(self, course_id, cells, occurrence_id):
        pairs_by_topic = {}
        lessons = self.service.courses.list_lessons(course_id)
        if occurrence_id is not None:
            lessons = [lesson for lesson in lessons if lesson.id == occurrence_id]
            if not lessons:
                raise AttendanceError("Фактическое занятие не относится к выбранному курсу.")
        for occurrence in lessons:
            pairs_by_topic.setdefault(occurrence.topic_id, set()).update(
                self.service.redesign.get_block(occurrence.block_id).pair_ids
            )
        allowed = {
            (student_id, pair_id)
            for student_id, topic_id in cells
            for pair_id in pairs_by_topic.get(topic_id, set())
        }
        all_pairs = set().union(*pairs_by_topic.values()) if pairs_by_topic else set()
        return (
            (lambda student, pair: (student, pair) in allowed)
            if cells
            else (lambda _student, pair: pair in all_pairs)
        )

    def _manual_history(self, payload):
        course = course_for(self.service, payload)
        self._history_course_id = course.id
        occurrence_id = (
            integer(payload, "occurrence_id") if payload.get("occurrence_id") is not None else None
        )
        includes = self._manual_history_scope(course.id, self._cells(payload), occurrence_id)
        names = {student.id: student.full_name for student in self.service.list_students()}
        self._history = []
        for batch in self.service.redesign.list_manual_batches():
            targets = tuple(target for target in batch.targets if includes(*target))
            if not targets:
                continue
            revocations = tuple(
                (student, pair, at)
                for student, pair, at in batch.revoked_targets
                if includes(student, pair)
            )
            active = any(includes(*target) for target in batch.active_targets)
            revoked_at = (
                None
                if active
                else batch.revoked_at or max((at for _, _, at in revocations), default=None)
            )
            self._history.append(
                dict(
                    json_safe(batch),
                    name=f"{batch.created_at.astimezone(ZoneInfo(self.timezone)):%d.%m.%Y %H:%M} · {batch.reason}",
                    students=sorted({names.get(student, student) for student, _ in targets}),
                    targets=json_safe(targets),
                    revoked_targets=json_safe(revocations),
                    revoked_at=json_safe(revoked_at),
                    active=active,
                )
            )
        return {"history": self._history}

    def _revoke(self, payload):
        course = course_for(self.service, payload)
        batch_id = integer(payload, "batch_id")
        cells = self._cells(payload)
        occurrence_id = (
            integer(payload, "occurrence_id") if payload.get("occurrence_id") is not None else None
        )
        includes = self._manual_history_scope(course.id, cells, occurrence_id)
        batch = next(
            (
                batch
                for batch in self.service.redesign.list_manual_batches()
                if batch.id == batch_id
            ),
            None,
        )
        if batch is None or not any(includes(sid, pair) for sid, pair in batch.targets):
            raise AttendanceError("Ручная отметка не относится к выбранному курсу.")
        if (
            not cells
            and occurrence_id is None
            and all(includes(*target) for target in batch.targets)
        ):
            self.service.redesign.revoke_manual_batch(batch_id)
        else:
            self.service.redesign.revoke_manual_targets(
                {(batch_id, sid, pair) for sid, pair in batch.active_targets if includes(sid, pair)}
            )
        self._manual_history(payload)
        return {"batch_id": batch_id}

    def _export_journal(self, payload):
        course = course_for(self.service, payload)
        if self.files is None:
            raise AttendanceError("Файловый адаптер не подключён.")
        headers, rows = self.service.grading.export_projection(
            course.id, observed_only=bool(payload.get("observed_only", False))
        )
        path = local_path(payload.get("path", ""))
        if path.suffix.lower() == ".csv":
            self.files.write_csv(path, headers, rows)
        else:
            self.files.export_xlsx(path, headers, rows)
        return {"path": str(path)}
