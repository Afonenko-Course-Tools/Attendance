import csv
from datetime import UTC, datetime, timedelta

from attendance_lab.domain.models import (
    AttendanceCell,
    AttendanceReport,
    AttendanceRow,
    Lesson,
    Student,
)
from attendance_lab.infrastructure.csv_io import (
    export_report,
    preview_students,
    read_csv,
    write_csv,
)


def test_csv_preview_validates_before_mutation(tmp_path):
    source = tmp_path / "students.csv"
    source.write_text(
        "id;ФИО;Группа\n001;Тест Первый;A\n001;Тест Второй;A\n002;;B\n", encoding="utf-8-sig"
    )
    headers, rows = read_csv(source)
    result = preview_students(headers, rows, 0, 1, 2)
    assert len(result.errors) == 2
    assert result.students[0].id == "001"


def test_generated_ids_do_not_merge_same_names():
    result = preview_students(["name"], [["Тест"], ["Тест"]], None, 0, None)
    assert not result.errors
    assert result.students[0].id != result.students[1].id


def test_formula_export_and_embedded_csv_characters(tmp_path):
    target = tmp_path / "out.csv"
    write_csv(target, ["ФИО"], [["  =1+2"], ["Тест;Фамилия\nИмя"]])
    with target.open(encoding="utf-8-sig", newline="") as f:
        data = list(csv.reader(f, delimiter=";"))
    assert data[1] == ["'  =1+2"]
    assert data[2] == ["Тест;Фамилия\nИмя"]


def test_exports_are_safe_and_first_time_is_explicit(tmp_path):
    at = datetime(2026, 9, 7, 9, 3, tzinfo=UTC)
    lesson = Lesson(1, 1, at, at + timedelta(hours=1), "UTC", title="Практика")
    report = AttendanceReport(
        (lesson,),
        (AttendanceRow(Student("001", "Тест"), (AttendanceCell("present", at),)),),
        0,
        "Физика",
    )
    target = tmp_path / "report.csv"
    export_report(target, report, "presences")
    output = target.read_text(encoding="utf-8-sig")
    assert "UID" not in output
    assert "2026-09-07T09:03:00+00:00" in output
    assert "Физика" in output


def test_atomic_export_failure_keeps_old_file(tmp_path):
    path = tmp_path / "important.csv"
    path.write_text("original", encoding="utf-8")

    def broken_rows():
        yield ["valid"]
        raise RuntimeError("disk-like failure")

    import pytest

    with pytest.raises(RuntimeError):
        write_csv(path, ["name"], broken_rows())
    assert path.read_text() == "original"
    assert len(list(tmp_path.iterdir())) == 1
