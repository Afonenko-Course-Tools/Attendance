"""Import must expose earlier unknown presentations without manufacturing events."""

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.sqlite import SQLiteRepository

HEADERS = [
    "№",
    "ФИО",
    "Специальность",
    "Специализация/профилизация",
    "Курс",
    "Группа",
    "№ зачётки",
    "Средний балл всех сессий",
]


def test_unknown_history_before_import_survives_late_match_correction_and_restart(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    discipline = service.save_discipline("Синтетический курс")
    first = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
    lesson = service.save_lesson(discipline.id, first, first + timedelta(minutes=80), "UTC")
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, "00010203", first + timedelta(minutes=3))
    service.record_scan(lesson.id, "00010203", first + timedelta(minutes=8))
    service.close_lesson(lesson.id)
    service.close()

    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    api = service.redesign
    plan = api.preview_import(
        HEADERS,
        [
            ["1", "Вымышленный Первый", "Тестовая специальность", "", "3", "1", "00001", "7.5"],
            ["2", "Вымышленный Второй", "Тестовая специальность", "", "3", "1", "00002", "0"],
        ],
        2026,
    )
    assert not plan.errors
    api.apply_import(plan)
    students = {c.record_book_number: c.student_id for c in api.list_student_contexts()}
    before = service.lesson_log(lesson.id)[0]
    service.bind_card("00010203", students["00001"])
    assert service.lesson_log(lesson.id)[0].student_id == students["00001"]
    service.bind_card(
        "00010203", students["00002"], correction=True, expected_student_id=students["00001"]
    )
    service.unbind_card("00010203", expected_student_id=students["00002"])
    service.close()

    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    after = service.lesson_log(lesson.id)
    assert len(after) == 1
    assert after[0].uid == "00010203" and after[0].student_id is None
    assert (after[0].first_seen, after[0].last_seen) == (before.first_seen, before.last_seen)
    service.bind_card("00010203", students["00001"])
    assert service.lesson_log(lesson.id)[0].first_seen == first + timedelta(minutes=3)
    service.close()


def test_public_demo_is_the_user_format_and_can_be_imported_twice(tmp_path):
    path = Path(__file__).parents[1] / "examples/demo.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        headers, *rows = csv.reader(stream)
    assert headers == HEADERS
    service = AttendanceService(SQLiteRepository(tmp_path / "demo.sqlite3", mode="demo"))
    api = service.redesign
    for _ in range(2):
        plan = api.preview_import(headers, rows, 2026)
        assert not plan.errors
        api.apply_import(plan)
    contexts = api.list_student_contexts()
    assert len(contexts) == len(rows)
    assert any(c.record_book_number.startswith("0000") for c in contexts)
    assert {2, 3} <= {c.course for c in contexts}
    service.close()
