"""Validated transport values shared by production Qt Quick application adapters."""

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from attendance_lab.application.lesson_choices import LessonChoice
from attendance_lab.application.list_queries import StudentSearch, student_search_fields
from attendance_lab.domain.models import AttendanceError


def json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def lesson_record(lesson: LessonChoice) -> dict:
    return {
        **json_safe(lesson),
        "label": lesson.label,
        "name": lesson.label,
        "date": lesson.date.isoformat(),
        "time": lesson.time,
        "deleted": lesson.deleted,
    }


def integer(payload, key, default=None):
    value = payload.get(key, default)
    if value is None or value == "":
        if default is None:
            raise AttendanceError(f"Укажите значение: {key}.")
        return default
    if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
        raise AttendanceError(f"Укажите целое число: {key}.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AttendanceError(f"Укажите целое число: {key}.") from exc


def optional_id(payload, key):
    return None if payload.get(key) in (None, "", 0, -1) else integer(payload, key)


def selected_ids(payload, key, *, numeric=True):
    values = payload.get(key, [])
    if not isinstance(values, (list, tuple)):
        raise AttendanceError("Ожидается список выбранных ID.")
    return {integer({"id": value}, "id") if numeric else str(value) for value in values}


def calendar_date(payload, key="date"):
    try:
        return date.fromisoformat(str(payload[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AttendanceError("Укажите дату в формате ГГГГ-ММ-ДД.") from exc


def local_path(value):
    parsed = urlparse(str(value))
    if parsed.scheme and parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise AttendanceError("Выберите локальный файл.")
    return Path(unquote(parsed.path) if parsed.scheme else str(value)).expanduser()


def _digest(headers, rows):
    return sha256(json.dumps([headers, rows], ensure_ascii=False).encode()).hexdigest()


def course_for(service, payload):
    return service.courses.get_course(integer(payload, "course_id"))


def lesson_for(service, payload):
    course = course_for(service, payload)
    lesson = service.courses.get_lesson(integer(payload, "lesson_id"))
    if lesson.course_id != course.id:
        raise AttendanceError("Занятие не относится к выбранному курсу.")
    return course, lesson


class SearchIndex:
    def __init__(self):
        self._search_indexes = {}

    def find(self, rows, query):
        fingerprint = tuple((row["id"], *student_search_fields(row)) for row in rows)
        if fingerprint not in self._search_indexes:
            if len(self._search_indexes) >= 8:
                self._search_indexes.pop(next(iter(self._search_indexes)))
            self._search_indexes[fingerprint] = StudentSearch(rows)
        ids = {row["id"]: row for row in rows}
        return [ids[row["id"]] for row in self._search_indexes[fingerprint].find(query)]
