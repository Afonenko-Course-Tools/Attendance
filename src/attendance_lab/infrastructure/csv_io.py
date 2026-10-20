"""CSV transport: preview before importing, atomic exports, no SQL or Qt."""

import csv
import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from attendance_lab.application.files import ImportPreview
from attendance_lab.domain.models import (
    AttendanceCell,
    AttendanceReport,
    Lesson,
    LogRow,
    Student,
)


def read_csv(
    path: Path, encoding: str = "utf-8-sig", delimiter: str = ";"
) -> tuple[list[str], list[list[str]]]:
    if delimiter not in (";", ",", "\t"):
        raise ValueError("Выберите точку с запятой, запятую или табуляцию")
    with Path(path).open(encoding=encoding, newline="") as stream:
        reader = csv.reader(stream, delimiter=delimiter, strict=True)
        try:
            headers = next(reader)
        except StopIteration:
            raise ValueError("Файл CSV пуст") from None
        rows = [row for row in reader if any(value.strip() for value in row)]
    if not headers or not any(value.strip() for value in headers):
        raise ValueError("В CSV нет заголовка")
    return [value.strip().lstrip("\ufeff") for value in headers], rows


def preview_students(
    headers: list[str],
    rows: list[list[str]],
    id_column: int | None,
    name_column: int | None,
    group_column: int | None,
) -> ImportPreview:
    errors: list[str] = []
    students: list[Student] = []
    seen: set[str] = set()
    if name_column is None:
        return ImportPreview((), ("Выберите колонку ФИО",))
    selected = [i for i in (id_column, name_column, group_column) if i is not None]
    if any(i < 0 or i >= len(headers) for i in selected):
        return ImportPreview((), ("Неверно выбрана колонка",))
    if len(selected) != len(set(selected)):
        return ImportPreview((), ("Для разных полей выберите разные колонки",))
    for number, row in enumerate(rows, start=2):
        if len(row) != len(headers):
            errors.append(f"Строка {number}: {len(row)} полей вместо {len(headers)}")
            continue
        name = row[name_column].strip()
        if not name:
            errors.append(f"Строка {number}: пустое ФИО")
            continue
        sid = row[id_column].strip() if id_column is not None else ""
        sid = sid or str(uuid4())
        if sid in seen:
            errors.append(f"Строка {number}: повтор ID {sid}")
            continue
        seen.add(sid)
        group = row[group_column].strip() if group_column is not None else ""
        students.append(Student(sid, name, group))
    if not rows:
        errors.append("В CSV нет строк студентов")
    return ImportPreview(tuple(students), tuple(errors))


def _safe(value: object) -> str:
    text = "" if value is None else str(value)
    # Protect also formula prefixes hidden behind whitespace/control characters.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def write_csv(
    path: Path,
    headers: Sequence[object],
    rows: Iterable[Sequence[object]],
    delimiter: str = ";",
    bom: bool = True,
) -> None:
    path = Path(path)
    if delimiter not in (";", ",", "\t"):
        raise ValueError("Неподдерживаемый разделитель CSV")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig" if bom else "utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            writer = csv.writer(stream, delimiter=delimiter)
            writer.writerow([_safe(value) for value in headers])
            for row in rows:
                writer.writerow([_safe(value) for value in row])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def cell_text(cell: AttendanceCell, timezone: str) -> str:
    if cell.state == "present" and cell.first_seen:
        at = cell.first_seen.astimezone(ZoneInfo(timezone))
        suffix = " (вне списка)" if cell.outside_roster else ""
        return f"{'Подтверждено вручную' if cell.manual_confirmation else 'Был'}, {at:%H:%M:%S}{suffix}"
    return {
        "missing": "Нет отметки",
        "expected": "Ожидается",
        "pending": "Пока нет отметки",
        "outside": "—",
    }.get(cell.state, "—")


def export_report(
    path: Path,
    report: AttendanceReport,
    kind: str = "matrix",
    delimiter: str = ";",
    bom: bool = True,
) -> None:
    headers: list[object]
    rows: list[Sequence[object]] = []
    if kind == "matrix":
        headers = ["Дисциплина", "student_id", "ФИО", "Группа"]
        for lesson in report.lessons:
            at = lesson.starts_at.astimezone(ZoneInfo(lesson.timezone))
            headers.append(f"{at.isoformat()} [{lesson.timezone}] {lesson.title} (#{lesson.id})")
        rows = [
            [report.discipline_name, row.student.id, row.student.full_name, row.student.group]
            + [
                cell_text(cell, lesson.timezone)
                for cell, lesson in zip(row.cells, report.lessons, strict=True)
            ]
            for row in report.rows
        ]
    elif kind == "presences":
        headers = [
            "Дисциплина",
            "discipline_id",
            "lesson_id",
            "Занятие",
            "Начало",
            "Часовой пояс",
            "student_id",
            "ФИО",
            "Группа",
            "Первое предъявление",
            "Вне состава",
        ]
        for row in report.rows:
            for cell, lesson in zip(row.cells, report.lessons, strict=True):
                if cell.first_seen is not None:
                    rows.append(
                        [
                            report.discipline_name,
                            lesson.discipline_id,
                            lesson.id,
                            lesson.title,
                            lesson.starts_at.isoformat(),
                            lesson.timezone,
                            row.student.id,
                            row.student.full_name,
                            row.student.group,
                            cell.first_seen.astimezone(ZoneInfo(lesson.timezone)).isoformat(),
                            "Да" if cell.outside_roster else "Нет",
                        ]
                    )
    else:
        raise ValueError("Неизвестный вид экспорта")
    write_csv(path, headers, rows, delimiter, bom)


def export_log(
    path: Path, lesson: Lesson, rows: list[LogRow], delimiter: str = ";", bom: bool = True
) -> None:
    zone = ZoneInfo(lesson.timezone)
    write_csv(
        path,
        [
            "lesson_id",
            "UID",
            "student_id",
            "ФИО",
            "Первое предъявление",
            "Последнее предъявление",
            "Часовой пояс",
        ],
        (
            [
                lesson.id,
                row.uid,
                row.student_id,
                row.full_name or "Не сопоставлена",
                row.first_seen.astimezone(zone).isoformat(),
                row.last_seen.astimezone(zone).isoformat(),
                lesson.timezone,
            ]
            for row in rows
        ),
        delimiter,
        bom,
    )


class CsvFiles:
    """Stateless adapter, injected by the composition root."""

    read_csv = staticmethod(read_csv)
    preview_students = staticmethod(preview_students)
    write_csv = staticmethod(write_csv)
    export_report = staticmethod(export_report)
    export_log = staticmethod(export_log)
