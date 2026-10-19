"""Spreadsheet CSV exports retain integer identity and detect separators."""

import csv

import pytest
from PySide6.QtWidgets import QApplication

from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.preparation import ImportPreviewDialog

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
    result = AttendanceService(SQLiteRepository(tmp_path / "synthetic.sqlite3", mode="demo"))
    yield result
    result.close()


def test_original_export_headers_ignore_average_and_preserve_student_identity(service, tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "synthetic-original.csv"
    path.write_text(
        "№,ФИО,Специальность,Специализация/профилизация,Курс,Группа,№ зачётки,"
        "Средний балл всех сессий\ufffc\n"
        '1,Тестов Алексей,"Физика, экспериментальная",Учебный профиль,2,10,000123,3.75\n',
        encoding="utf-8-sig",
    )
    dialog = ImportPreviewDialog(service, lambda: None)
    try:
        dialog.path.setText(str(path))
        dialog.review()
        assert dialog.plan is not None and not dialog.plan.errors
        service.redesign.apply_import(dialog.plan)
        first = service.redesign.list_student_contexts()[0]
        assert first.record_book_number == "000123"
        assert (first.course, first.group_number, first.profile) == (2, 10, "Учебный профиль")
        canonical = service.redesign.preview_import(
            HEADERS,
            [
                [
                    "1",
                    "Тестов Алексей",
                    "Физика, экспериментальная",
                    "Учебный профиль",
                    "2",
                    "10",
                    "000123",
                ]
            ],
            dialog.year.value(),
        )
        service.redesign.apply_import(canonical)
        assert service.redesign.list_student_contexts() == [first]
    finally:
        dialog.close()
    assert app is not None


def test_unrecognized_extra_column_is_not_silently_discarded(service):
    assert service.redesign.preview_import(
        HEADERS + ["Неизвестная колонка"],
        [["1", "Тестов Алексей", "Физика", "", "2", "10", "000123", "данные"]],
        2026,
    ).errors


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_preview_detects_export_separator_and_preserves_quoted_fields(service, tmp_path, delimiter):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "synthetic.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerow(HEADERS)
        writer.writerow(
            ["1", "Тестов Алексей", "Физика, экспериментальная", "", "2", "10", "000123"]
        )
    dialog = ImportPreviewDialog(service, lambda: None)
    try:
        dialog.path.setText(str(path))
        dialog.review()
        assert dialog.plan is not None and not dialog.plan.errors
        assert dialog.plan.rows[0].specialty == "Физика, экспериментальная"
        assert dialog.plan.rows[0].record_book_number == "000123"
    finally:
        dialog.close()
    assert app is not None
