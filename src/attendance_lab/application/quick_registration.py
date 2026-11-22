"""Registration queries and explicit card commands; independent from Qt."""

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from attendance_lab.application.course_absences import expected_student_ids
from attendance_lab.application.list_queries import StudentSearch
from attendance_lab.application.quick_common import plain
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.rules import normalize_uid

if TYPE_CHECKING:
    from attendance_lab.application.controller import AttendanceController
    from attendance_lab.application.service import AttendanceService


MUTATING_COMMANDS = frozenset({"registration.bind", "registration.unbind"})


class RegistrationActions:
    def __init__(self, service: AttendanceService, controller: AttendanceController):
        self.service = service
        self.controller = controller
        self._query = ""
        self._search_ids: tuple[str, ...] | None = None
        self.last_scan: dict | None = None

    def search(self, query: str) -> None:
        students = self.service.redesign.list_student_contexts()
        found = StudentSearch(students).find(query)
        self._query = query
        self._search_ids = tuple(student.student_id for student in found)

    def snapshot(self, course_id: int | None, lesson_id: int | None) -> dict:
        service, control = self.service, self.controller
        students = service.redesign.list_student_contexts()
        cards = service.list_cards()
        revisions = {}
        for change in service.repository.list_binding_changes():
            revisions.setdefault(change.uid, change.id)
        card_rows = [
            {**plain(card), "binding_revision": revisions.get(card.uid, 0)} for card in cards
        ]
        lesson = (
            next(
                (item for item in service.courses.list_lessons(course_id) if item.id == lesson_id),
                None,
            )
            if course_id is not None
            else None
        )
        pairs = service.redesign.get_block(lesson.block_id).pair_ids if lesson else ()
        logs = []
        for pair_id in pairs:
            for row in service.lesson_log(pair_id):
                logs.append({**plain(row), "pair_id": pair_id})
        expected = (
            set(expected_student_ids(service, course_id, lesson.block_id))
            if lesson is not None and course_id is not None
            else set()
        )
        roster_rows = []
        evidence = []
        if lesson:
            contexts = {s.student_id: s for s in students}
            people = {s.id: s for s in service.list_students()}
            zone = ZoneInfo(service.redesign.get_block(lesson.block_id).timezone)
            recorded = [
                e
                for pair in pairs
                for e in service.redesign.pair_evidence(pair, include_excused=True)
                if e.active
            ]
            excused = {e.student_id for e in recorded if e.kind == "excused"}
            evidence = [e for e in recorded if e.kind != "excused"]
            ids = expected.union(e.student_id for e in evidence if e.student_id is not None)
            for sid in ids:
                student = people.get(sid)
                if student is None:
                    continue
                facts = [e for e in evidence if e.student_id == sid]
                card_facts = [e for e in facts if e.kind != "manual"]
                first = min(card_facts or facts, key=lambda e: e.at) if facts else None
                context = contexts.get(sid)
                attendance_text = "У" if sid in excused else "Нет отметки"
                if first is not None:
                    prefix = "Ручное подтверждение" if not card_facts else "Карта"
                    at = first.at.astimezone(zone)
                    attendance_text = f"{prefix} {at:%H:%M}"
                roster_rows.append(
                    {
                        "id": sid,
                        "name": student.full_name,
                        "group": getattr(context, "group_number", student.group),
                        "record_book_number": getattr(context, "record_book_number", ""),
                        "study_course": getattr(context, "course", None),
                        "specialty": getattr(context, "specialty", ""),
                        "profile": getattr(context, "profile", ""),
                        "attendance_text": attendance_text,
                        "present": bool(facts),
                        "outside": sid not in expected,
                        "first_seen": min(e.at for e in facts).isoformat() if facts else "",
                    }
                )
        active_label = "Регистрация закрыта"
        if control.active_session_id is not None:
            session = service.redesign.get_session(control.active_session_id)
            block = service.redesign.get_block(session.block_id)
            active_label = f"{block.title} · {block.date.isoformat()}"
        elif control.active_lesson_id is not None:
            item = service.get_lesson(control.active_lesson_id)
            active_label = item.title
        found_ids = set(self._search_ids) if self._search_ids is not None else None
        return {
            "active_session_id": control.active_session_id,
            "active_lesson_id": control.active_lesson_id,
            "active_label": active_label,
            "open": control.active_lesson_id is not None,
            "pending_count": getattr(control, "pending_count", 0),
            "blocked": control.blocked,
            "closing": control.active_lesson_id is not None and not control.accepting,
            "cards": card_rows,
            "unknown": [card for card in card_rows if card["student_id"] is None],
            "students": [
                {**plain(s), "id": s.student_id, "name": s.full_name}
                for s in students
                if found_ids is None or s.student_id in found_ids
            ],
            "query": self._query,
            "rows": sorted(roster_rows, key=lambda row: row["name"]),
            "present_count": sum(row["present"] for row in roster_rows),
            "expected_count": len(expected),
            "unknown_count": len({e.uid for e in evidence if e.student_id is None and e.uid}),
            "logs": logs,
            "last_scan": self.last_scan,
        }

    def execute(self, name: str, payload: dict) -> dict:
        if name == "registration.search":
            self.search(str(payload.get("query", "")))
            return {}
        uid = normalize_uid(str(payload.get("uid", "")))
        expected = payload.get("expected_student_id")
        if name in ("registration.bind", "registration.unbind"):
            current = self.service.repository.get_card(uid)
            if current is None or current.student_id != expected:
                raise AttendanceError("Привязка карты изменилась. Обновите выбранную карту.")
            history = self.service.repository.list_binding_changes(uid)
            revision = history[0].id if history else 0
            if int(payload.get("binding_revision", 0)) != revision:
                raise AttendanceError("История привязки изменилась. Выберите карту повторно.")
        if name == "registration.bind":
            student_id = str(payload["student_id"])
            self.service.bind_card(
                uid,
                student_id,
                correction=bool(payload.get("correction", False)),
                expected_student_id=expected,
            )
            return {
                "uid": uid,
                "student_id": student_id,
                "student_name": self.service._student(student_id).full_name,
            }
        if name == "registration.unbind":
            self.service.unbind_card(uid, expected_student_id=str(expected or ""))
            return {"uid": uid}
        raise AttendanceError("Неизвестная команда регистрации.")
