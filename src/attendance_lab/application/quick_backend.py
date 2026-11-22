"""One sequential application session; created and used by its owning executor."""

from dataclasses import dataclass
from typing import cast

from attendance_lab.application.operations import (
    Operation,
    OperationExecutor,
    OperationRepository,
)
from attendance_lab.application.quick_academic import MUTATING_COMMANDS as ACADEMIC_WRITES
from attendance_lab.application.quick_academic import AcademicActions
from attendance_lab.application.quick_grading import MUTATING_COMMANDS as GRADING_WRITES
from attendance_lab.application.quick_grading import GradingActions
from attendance_lab.application.quick_registration import MUTATING_COMMANDS as REGISTRATION_WRITES
from attendance_lab.application.quick_registration import RegistrationActions
from attendance_lab.application.quick_system import MUTATING_COMMANDS as SYSTEM_WRITES
from attendance_lab.application.quick_system import SystemActions
from attendance_lab.domain.models import AttendanceError, PublicResult


@dataclass(frozen=True, slots=True)
class ViewContext:
    course_id: int | None = None
    lesson_id: int | None = None
    revision: int = 0


class QuickBackend:
    def __init__(self, service, controller, files, *, demo=False, timezone="Europe/Minsk"):
        self.service, self.controller = service, controller
        self.academic = AcademicActions(service, files=files, timezone=timezone)
        self.grading = GradingActions(service, files=files)
        self.registration = RegistrationActions(service, controller)
        self.system = SystemActions(service, controller, files, demo=demo)
        self.operations = OperationExecutor(cast(OperationRepository, service.repository))
        self._public: list[PublicResult] = []
        self._dirty = False
        self.system.recovered = service.recover_interrupted()
        self.system.readers = controller.available_readers()

    def poll(self) -> bool:
        dirty = False
        for event in self.controller.drain():
            if event.kind == "public" and event.result is not None:
                self._public.append(event.result)
            elif event.kind == "status":
                self.system.status = event.text
                dirty = True
            elif event.kind == "changed":
                if event.scan is not None:
                    self.registration.last_scan = {
                        "uid": event.scan.uid,
                        "at": event.scan.at.isoformat(),
                        "lesson_id": event.scan.lesson_id,
                        "session_id": event.scan.session_id,
                        "kind": event.result.kind if event.result else "success",
                        "name": event.result.full_name if event.result else "",
                    }
                dirty = True
            elif event.kind == "failed":
                dirty = True
        self._dirty = self._dirty or dirty
        return dirty

    def public_results(self) -> tuple[PublicResult, ...]:
        results = tuple(self._public)
        self._public.clear()
        return results

    def snapshot(self, context: ViewContext) -> dict:
        courses = self.service.courses.list_courses()
        course_id = context.course_id
        if course_id not in {course.id for course in courses}:
            course_id = courses[0].id if courses else None
        lesson_id = context.lesson_id
        lessons = self.service.courses.list_lessons(course_id) if course_id is not None else []
        if lesson_id not in {lesson.id for lesson in lessons}:
            lesson_id = None
        state = self.academic.snapshot(course_id, lesson_id)
        state["selected_course_id"] = course_id
        state["selected_lesson_id"] = lesson_id
        state["registration"] = self.registration.snapshot(course_id, lesson_id)
        state["system"] = self.system.snapshot()
        state["grading"] = (
            self.grading.snapshot(course_id, lesson_id) if course_id is not None else {}
        )
        self._dirty = False
        return state

    def command(self, operation: Operation) -> dict:
        import json

        payload = json.loads(operation.payload_json)
        if not isinstance(payload, dict):
            raise AttendanceError("Параметры команды должны быть объектом.")
        name = operation.kind
        self.poll()
        if name == "operation.resolve":
            with self.service.repository.transaction(write=False):
                receipt = self.operations.repository.get_operation_receipt(
                    str(payload["operation_id"])
                )
            return {"committed": receipt is not None, "operation_id": payload["operation_id"]}
        if name == "registration.retry":
            if not self.controller.retry_pending():
                raise AttendanceError("Принятые предъявления ещё не сохранены.")
            return {}
        if name == "registration.open":
            course_id, lesson_id = int(payload["course_id"]), int(payload["lesson_id"])
            lesson = next(
                (
                    item
                    for item in self.service.courses.list_lessons(course_id)
                    if item.id == lesson_id
                ),
                None,
            )
            if lesson is None:
                raise AttendanceError("Выбранное занятие больше недоступно.")
            block = self.service.redesign.get_block(lesson.block_id)
            session_id = self.controller.open_session(
                block.id, set(block.pair_ids), operation_id=operation.operation_id
            )
            return {"session_id": session_id}
        if name == "registration.open_legacy":
            lesson_id = int(payload["lesson_id"])
            if lesson_id not in {item.id for item in self.service.list_source_lessons()}:
                raise AttendanceError("Выберите занятие архива вне курсов.")
            self.controller.open_lesson(lesson_id, operation_id=operation.operation_id)
            return {}
        if name == "registration.close":
            if payload.get("session_id") is not None:
                self.controller.close_session(
                    operation_id=operation.operation_id, session_id=int(payload["session_id"])
                )
            else:
                self.controller.close_lesson(
                    operation_id=operation.operation_id, lesson_id=payload.get("lesson_id")
                )
            return {}
        actions = (
            self.academic
            if name.startswith(("academic.", "journal.", "students."))
            else self.grading
            if name.startswith("grading.")
            else self.registration
            if name.startswith("registration.")
            else self.system
        )
        writes = ACADEMIC_WRITES | GRADING_WRITES | REGISTRATION_WRITES | SYSTEM_WRITES
        if name in writes:
            if self.controller.blocked:
                raise AttendanceError(
                    "Сначала подтвердите сохранение принятых карт: нажмите «Повторить запись»."
                )
            # File exports replace deterministic output and do not belong in a DB transaction.
            if name.endswith("export"):
                return actions.execute(name, payload)
            result = self.operations.execute(operation, lambda: actions.execute(name, payload))
            if actions is self.academic:
                self.academic.restore_result(name, result)
            return cast(dict, result)
        return actions.execute(name, payload)

    def close(self) -> None:
        self.controller.shutdown()
        self.service.close()
