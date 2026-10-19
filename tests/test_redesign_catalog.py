"""Catalog integrity tests, always using synthetic temporary data."""

import sqlite3

import pytest

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SQLiteRepository

HEADERS = [
    "Внутренний № по списку",
    "ФИО",
    "Специальность",
    "Профилизация",
    "Курс",
    "Группа",
    "Номер зачётки",
]


@pytest.fixture
def service(tmp_path):
    result = AttendanceService(SQLiteRepository(tmp_path / "synthetic.sqlite3"))
    yield result
    result.close()


def load(api, rows, year=2026, **options):
    plan = api.preview_import(HEADERS, rows, year, **options)
    assert not plan.errors, plan.errors
    api.apply_import(plan)
    return api.list_student_contexts()


def test_alias_resolution_preserves_identity_and_blocks_cross_catalog_collision(service):
    api = service.redesign
    rows = [["1", "Тестов Один", " Информатика  ", "Системы", "1", "1", "0001"]]
    first = load(api, rows)[0]
    specialty = api.list_specialties()[0]
    api.add_specialty_alias(specialty.id, "Вычисления")
    load(api, [["1", "Тестов Исправленный", "вычисления", "системы", "1", "1", "0001"]])
    after = api.list_student_contexts()[0]
    assert after.student_id == first.student_id
    assert after.full_name == "Тестов Исправленный"
    assert len(api.list_specialties()) == 1
    load(api, [["1", "Другой", "Физика", "", "1", "1", "0002"]])
    other = next(s for s in api.list_specialties() if s.name == "Физика")
    with pytest.raises(AttendanceError):
        api.add_specialty_alias(other.id, "информатика")
    with pytest.raises(AttendanceError):
        api.rename_specialty(other.id, "Вычисления")


def test_ambiguous_groups_require_explicit_choice(service):
    api = service.redesign
    load(api, [["1", "Тестов", "Специальность", "", "1", "1", "001"]])
    year = api.list_years()[0]
    specialty = api.list_specialties()[0]
    alternate = api.save_group(year.id, specialty.id, 1, 1, "вечерняя")
    rows = [["1", "Новый", "Специальность", "", "1", "1", "002"]]
    assert api.preview_import(HEADERS, rows, 2026).errors
    load(api, rows, group_choices={2: alternate.id})
    assert (
        next(s for s in api.list_student_contexts() if s.record_book_number == "002").group_id
        == alternate.id
    )


def test_membership_fk_rejects_wrong_year_and_wrong_profile_specialty(service):
    api = service.redesign
    load(
        api,
        [
            ["1", "Первый", "A", "Профиль", "1", "1", "01"],
            ["2", "Второй", "B", "Профиль", "1", "1", "02"],
        ],
    )
    year = api.save_year(2027)
    first, second = sorted(api.list_student_contexts(), key=lambda s: s.record_book_number)
    other_profile = next(
        p for p in api.list_profiles() if p.specialty_id != api.list_groups()[0].specialty_id
    )
    with sqlite3.connect(service.repository.path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE annual_memberships SET academic_year_id=? WHERE student_id=?",
                (year.id, first.student_id),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE annual_memberships SET profile_id=? WHERE student_id=?",
                (other_profile.id, first.student_id),
            )
