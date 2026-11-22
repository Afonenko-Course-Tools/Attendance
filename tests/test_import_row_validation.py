"""CSV regressions use temporary synthetic databases only."""

import pytest

from attendance_lab.application.redesign_catalog import IMPORT_HEADERS
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SQLiteRepository

HEADERS = [*IMPORT_HEADERS, "Средний балл всех сессий"]


@pytest.fixture
def api(tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "synthetic.sqlite3"))
    yield service.redesign
    service.close()


def student(number="0001", course="3"):
    return ["1", "Синтетический Студент", "Специальность", "", course, "1", number]


def test_large_third_course_with_blank_tail_preserves_existing_second_course(api):
    old_plan = api.preview_import(list(IMPORT_HEADERS), [student("0002", "2")], 2026)
    assert not old_plan.errors
    api.apply_import(old_plan)
    old_context = api.list_student_contexts()[0]
    rows = [student(f"003{number:04}") + ["8,5"] for number in range(228)]
    rows += [[], ["", " ", "\t"], [" "] * 8]
    plan = api.preview_import(HEADERS, rows, 2026)
    assert not plan.errors
    assert len(plan.rows) == 228
    assert plan.rows[-1].source_row == 229
    api.apply_import(plan)
    contexts = api.list_student_contexts()
    assert len(contexts) == 229
    assert next(c for c in contexts if c.record_book_number == "0002") == old_context
    third_group_ids = {g.id for g in api.list_groups() if g.course == 3}
    assert sum(c.group_id in third_group_ids for c in contexts) == 228


@pytest.mark.parametrize("rows", [[], [[], [" ", "\t"], [""] * 8]])
def test_file_without_students_is_rejected(api, rows):
    plan = api.preview_import(HEADERS, rows, 2026)
    assert "В CSV нет строк студентов." in plan.errors
    with pytest.raises(AttendanceError):
        api.apply_import(plan)
    assert not api.list_student_contexts()


def test_blank_rows_preserve_original_line_numbers_and_duplicate_diagnostics(api):
    plan = api.preview_import(HEADERS, [[], student() + [""], [" "], student() + [""]], 2026)
    assert [row.source_row for row in plan.rows] == [3]
    assert any("строки 3 и 5" in error for error in plan.errors)


def test_optional_average_can_be_omitted_without_relaxing_required_columns(api):
    plan = api.preview_import(HEADERS, [student(), student("0002") + ["не используется"]], 2026)
    assert not plan.errors
    assert [row.record_book_number for row in plan.rows] == ["0001", "0002"]
    api.apply_import(plan)
    assert len(api.list_student_contexts()) == 2


@pytest.mark.parametrize(
    "headers,width,expected",
    [(HEADERS, 6, "7 или 8"), (HEADERS, 9, "7 или 8"), (list(IMPORT_HEADERS), 8, "7")],
)
def test_nonempty_wrong_width_blocks_import(api, headers, width, expected):
    row = (student() + ["extra"] * 2)[:width]
    plan = api.preview_import(headers, [[], row], 2026)
    assert plan.errors == (f"Строка 3: получено {width} полей, ожидается {expected}.",)
    with pytest.raises(AttendanceError):
        api.apply_import(plan)
    assert not api.list_student_contexts()


@pytest.mark.parametrize("index,value", [(0, "0"), (4, "-1"), (5, "x"), (6, "１２３")])
def test_optional_average_does_not_relax_numbers_or_identity(api, index, value):
    row = student()
    row[index] = value
    plan = api.preview_import(HEADERS, [[], row], 2026)
    assert plan.errors
    assert all("Строка 3:" in error for error in plan.errors)
    with pytest.raises(AttendanceError):
        api.apply_import(plan)


def test_public_name_resolution_normalizes_names_and_respects_specialty(api):
    row = student()
    row[3] = "Профиль"
    plan = api.preview_import(list(IMPORT_HEADERS), [row], 2026)
    api.apply_import(plan)
    specialty = api.list_specialties()[0]
    profile = api.list_profiles()[0]
    api.add_specialty_alias(specialty.id, "Псевдоним")
    api.add_profile_alias(profile.id, "Направление")
    assert api.resolve_specialty_name("  СПЕЦИАЛЬНОСТЬ ") == specialty.id
    assert api.resolve_specialty_name(" псевдоним ") == specialty.id
    assert api.resolve_specialty_name("Другая") is None
    assert api.resolve_profile_name(specialty.id, " ПРОФИЛЬ ") == profile.id
    assert api.resolve_profile_name(specialty.id, " направление ") == profile.id
    assert api.resolve_profile_name(specialty.id + 1, "Профиль") is None
