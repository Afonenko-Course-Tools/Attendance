"""Synthetic registration fixture; never opens an application database."""

from datetime import date

from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.sqlite import SQLiteRepository


def registration_service(path):
    service = AttendanceService(SQLiteRepository(path, mode="demo"))
    api = service.redesign
    headers = ["№", "ФИО", "Специальность", "Профилизация", "Курс", "Группа", "Номер зачётки"]
    rows = [["1", "Учебный Студент", "Учебная специальность", "", "2", "1", "0001"]]
    api.apply_import(api.preview_import(headers, rows, 2026))
    group = api.list_groups()[0]
    discipline = service.save_discipline("Учебная лабораторная")
    api.set_discipline_groups(discipline.id, group.academic_year_id, {group.id})
    roster = api.save_roster(
        discipline.id,
        group.academic_year_id,
        1,
        "Учебный состав",
        "lab",
        2,
        "groups",
        {group.id},
        set(),
    )
    block = api.schedule_block(roster.id, date(2026, 9, 7), 1)
    return service, block
