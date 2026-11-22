"""Compose independent course, journal and directory actions for one storage owner."""

from attendance_lab.application.quick_academic_common import json_safe as json_safe
from attendance_lab.application.quick_courses import MUTATING_COMMANDS as COURSE_WRITES
from attendance_lab.application.quick_courses import CourseActions
from attendance_lab.application.quick_journal import MUTATING_COMMANDS as JOURNAL_WRITES
from attendance_lab.application.quick_journal import JournalActions
from attendance_lab.application.quick_students import MUTATING_COMMANDS as STUDENT_WRITES
from attendance_lab.application.quick_students import StudentActions
from attendance_lab.domain.models import AttendanceError

MUTATING_COMMANDS = COURSE_WRITES | JOURNAL_WRITES | STUDENT_WRITES


class AcademicActions:
    def __init__(self, service, files=None, timezone="Europe/Minsk"):
        self.service = service
        self.courses = CourseActions(service, timezone)
        self.journal = JournalActions(service, files, timezone)
        self.students = StudentActions(service, files)

    def snapshot(self, course_id=None, lesson_id=None):
        with self.service.repository.transaction(write=False):
            catalogs = self.students.catalogs()
            state = self.courses.snapshot(course_id, lesson_id, catalogs)
            state["catalogs"] = catalogs
            state.update(self.students.snapshot(catalogs))
            selected_id = state["course"]["id"] if state["course"] else None
            state.update(self.journal.snapshot(selected_id))
            state["commands"] = self.courses.availability(
                selected_id, lesson_id, bool(state["journal"]["rows"])
            )
            return state

    def execute(self, name, payload):
        if not isinstance(payload, dict):
            raise AttendanceError("Некорректная команда.")
        owner = {"academic": self.courses, "journal": self.journal, "students": self.students}.get(
            name.split(".", 1)[0]
        )
        if owner is None:
            raise AttendanceError("Неизвестная учебная команда.")
        return owner.execute(name, payload)

    def restore_result(self, name, result):
        """Rehydrate transient presentation choices from a trusted durable receipt."""
        if name == "journal.prepare_mark":
            self.journal.restore_preparation(result)
