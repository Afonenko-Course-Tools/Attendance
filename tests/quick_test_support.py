"""Temporary production-Quick fixture composition shared by integration scenarios."""

from datetime import date

from controller_test_support import registration_service

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.quick_backend import QuickBackend
from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.infrastructure.sqlite import SQLiteRepository


def seed_workspace(path):
    service, old_block = registration_service(path)
    group = service.redesign.list_groups()[0]
    discipline = service.list_disciplines()[0]
    students = service.list_students()
    other_student = service.create_student("Учебный Поздний", student_id="late-student")
    courses, lessons = [], []
    for number in (1, 2):
        course = service.courses.save_course(
            discipline.id, group.academic_year_id, 1, 2, "lab", 2, f"Учебный курс {number}"
        )
        roster = service.courses.add_groups(course.id, {group.id})
        service.courses.save_topic(course.id, 1, f"Учебная тема {number}")
        lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, number + 10), 1)
        courses.append(course.id)
        lessons.append(lesson.id)
    service.bind_card("0011", students[0].id)
    service.close()
    return {
        "courses": tuple(courses),
        "lessons": tuple(lessons),
        "known_student": students[0].id,
        "late_student": other_student.id,
        "archive_pair": old_block.pair_ids[0],
        "discipline": discipline.id,
    }


def open_backend(path):
    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    controller = AttendanceController(service, DemoReader)
    return QuickBackend(service, controller, CsvFiles(), demo=True, timezone="UTC")
