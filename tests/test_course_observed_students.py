"""Observed rows are course-scoped evidence, never inferred expected enrollment."""

from datetime import UTC, date, datetime

import pytest

from attendance_lab.application.course_absences import course_absences, expected_student_ids
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import TeachingRoster
from attendance_lab.infrastructure.sqlite import SQLiteRepository

AT = datetime(2026, 9, 1, 6, 1, 23, 123456, UTC)


@pytest.fixture
def lab(tmp_path):
    service = AttendanceService(
        SQLiteRepository(tmp_path / "synthetic-observed.sqlite3", mode="demo")
    )
    headers = [
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    ]
    rows = [
        ["1", "Учебный Первый", "Пример", "Профиль", "2", "1", "0001"],
        ["2", "Учебный Второй", "Пример", "Профиль", "2", "1", "0002"],
        ["3", "Учебный Третий", "Пример", "Профиль", "2", "2", "0003"],
    ]
    service.redesign.apply_import(service.redesign.preview_import(headers, rows, 2026))
    year = service.redesign.list_years()[0]
    discipline = service.save_discipline("Учебная дисциплина")
    students = sorted(
        service.redesign.list_student_contexts(year.id), key=lambda s: s.record_book_number
    )

    def empty_course(semester=1, year_id=None):
        course = service.courses.save_course(
            discipline.id, year_id or year.id, semester, 2, "lab", 2, "Учебный курс"
        )
        with service.repository.transaction():
            roster = service.repository.rd_save_roster(
                TeachingRoster(
                    0,
                    discipline.id,
                    year_id or year.id,
                    semester,
                    "Исторический состав без назначения",
                    "lab",
                    2,
                    "students",
                    (),
                    (),
                )
            )
            service.courses.add_roster(course.id, roster.id)
        service.courses.save_topic(course.id, 1, "Учебная тема")
        occurrence = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
        return course, service.redesign.get_block(occurrence.block_id)

    course, block = empty_course()
    yield service, students, course, block, empty_course
    service.close()


def scan(service, block, uid, student_id=None):
    if student_id is not None:
        service.bind_card(uid, student_id)
    session = service.redesign.open_session(block.id, set(block.pair_ids))
    service.redesign.record_session_scan(session.id, uid, AT)
    service.redesign.close_session(session.id)


def ids(service, course):
    return {student.student_id for student in service.courses.list_observed_students(course.id)}


def test_only_identified_active_selected_course_evidence_is_displayed(lab):
    service, students, course, block, empty_course = lab
    other, other_block = empty_course(2)
    scan(service, block, "0000A001", students[0].student_id)
    scan(service, block, "0000A002")
    scan(service, other_block, "0000A003", students[1].student_id)
    assert ids(service, course) == {students[0].student_id}
    assert ids(service, other) == {students[1].student_id}
    assert service.courses.list_students(course.id) == []
    assert expected_student_ids(service, course.id, block.id) == set()
    absences = course_absences(service, course.id, now=datetime(2026, 11, 10, tzinfo=UTC))
    assert absences.incomplete and not absences.missed_topics
    assert service.grading.students(course.id) == ()
    with pytest.raises(AttendanceError):
        service.courses.list_observed_students(999999)


def test_late_binding_correction_and_unbinding_change_rows_without_new_scan(lab):
    service, students, course, block, _ = lab
    uid = "0000B001"
    scan(service, block, uid)
    assert ids(service, course) == set()
    service.bind_card(uid, students[0].student_id)
    assert ids(service, course) == {students[0].student_id}
    service.bind_card(
        uid, students[1].student_id, correction=True, expected_student_id=students[0].student_id
    )
    assert ids(service, course) == {students[1].student_id}
    service.unbind_card(uid, expected_student_id=students[1].student_id)
    assert ids(service, course) == set()
    evidence = service.repository.rd_pair_evidence(block.pair_ids[0])
    assert len(evidence) == 1 and evidence[0].at == AT


def test_multiple_cards_and_block_pairs_produce_one_row_per_student(lab):
    service, students, course, block, _ = lab
    scan(service, block, "0000C001", students[0].student_id)
    scan(service, block, "0000C002", students[0].student_id)
    scan(service, block, "0000C003", students[1].student_id)
    observed = service.courses.list_observed_students(course.id)
    assert len(observed) == 2
    assert {student.student_id for student in observed} == {s.student_id for s in students[:2]}
    assert [student.full_name for student in observed] == sorted(s.full_name for s in observed)


def test_deleted_occurrence_and_revoked_manual_evidence_are_excluded(lab):
    service, students, course, block, _ = lab
    scan(service, block, "0000D001", students[0].student_id)
    manual = service.redesign.create_manual_batch(
        {(students[1].student_id, block.pair_ids[0])}, "Учебное ручное подтверждение"
    )
    assert ids(service, course) == {s.student_id for s in students[:2]}
    service.redesign.revoke_manual_batch(manual.id)
    assert ids(service, course) == {students[0].student_id}
    deletion = service.lifecycle.delete_lesson(block.pair_ids[0], "Учебное удаление")
    assert ids(service, course) == set()
    service.lifecycle.restore_deletion(deletion.id)
    assert ids(service, course) == {students[0].student_id}


def test_identity_remains_visible_without_membership_in_selected_academic_year(lab):
    service, students, _, _, empty_course = lab
    new_year = service.redesign.save_year(2027)
    course, block = empty_course(1, new_year.id)
    scan(service, block, "0000E001", students[0].student_id)
    observed = service.courses.list_observed_students(course.id)
    assert len(observed) == 1
    assert observed[0].full_name == students[0].full_name
    assert observed[0].record_book_number == "0001"
    assert observed[0].academic_year_id is None
    assert observed[0].group_id is None and observed[0].group_number is None
    assert service.courses.list_students(course.id) == []


def test_observed_export_is_explicit_course_scoped_without_compulsory_grade_assumptions(
    lab, tmp_path
):
    from xml.etree import ElementTree as ET
    from zipfile import ZipFile

    from attendance_lab.infrastructure.grading_export import export_xlsx

    service, students, course, block, empty_course = lab
    other, other_block = empty_course(2)
    scan(service, block, "0000F001", students[0].student_id)
    scan(service, other_block, "0000F002", students[1].student_id)
    work = service.grading.add_work(course.id, "Контрольная", 20)
    headers, default_rows = service.grading.export_projection(course.id)
    assert default_rows == []
    observed_headers, rows = service.grading.export_projection(course.id, observed_only=True)
    assert observed_headers == headers
    assert len(rows) == 1 and rows[0][0] == students[0].full_name
    assert rows[0][headers.index("№ зачётки")] == "0001"
    assert (
        AT.isoformat() in rows[0][next(i for i, h in enumerate(headers) if h.startswith("Тема 1:"))]
    )
    assert rows[0][headers.index("Оценка доклада /10")] is None
    assert rows[0][headers.index(f"{work.title} /{work.maximum:g}")] is None
    assert rows[0][headers.index("Итог /10")] is None
    assert rows[0][headers.index("Полнота")] == "Состав не назначен"
    assert rows[0][headers.index("Версия расчёта")] is None
    assert service.grading.students(course.id) == ()
    assert service.grading.results(course.id) == ()
    assert service.courses.list_students(course.id) == []
    path = tmp_path / "observed.xlsx"
    export_xlsx(path, headers, rows)
    with ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert len(sheet.findall(".//{*}row")) == 2
        assert b"0001" in archive.read("xl/worksheets/sheet1.xml")
        assert len([name for name in archive.namelist() if name.startswith("xl/worksheets/")]) == 1


def test_observed_export_preserves_real_enrolled_zero_without_changing_default_population(lab):
    service, students, course, block, _ = lab
    service.courses.add_groups(course.id, {students[0].group_id})
    scan(service, block, "0000F003", students[0].student_id)
    work = service.grading.add_work(course.id, "Контрольная", 20)
    service.grading.save_scores(course.id, {(students[0].student_id, work.id): 0})
    headers, default_rows = service.grading.export_projection(course.id)
    _, observed_rows = service.grading.export_projection(course.id, observed_only=True)
    assert len(default_rows) == 2 and len(observed_rows) == 1
    assert observed_rows[0][headers.index("Итог /10")] == 0
    assert observed_rows[0][headers.index("Полнота")] == "Полный"
    assert observed_rows[0][headers.index(f"{work.title} /{work.maximum:g}")] == 0
