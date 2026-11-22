"""Shared search and typed sort/filter rules for the operator lists."""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from attendance_lab.application.text_search import TextSearch, normalized

STUDENT_SEARCH_FIELDS = (
    "full_name",
    "record_book_number",
    "group_number",
    "specialty",
    "profile",
    "subgroup",
)
REPORT_SORT_FIELDS = frozenset({"full_name", "group", "topic", "lesson", "grade"})


def _field(row: Any, name: str):
    return row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)


def student_search_fields(row: Any) -> tuple[str, ...]:
    """Accept student contexts and the small record mappings used by the views."""
    aliases = {"full_name": "name", "group_number": "group"}
    return tuple(
        str(_field(row, name) or (_field(row, aliases[name]) if name in aliases else "") or "")
        for name in STUDENT_SEARCH_FIELDS
    )


class StudentSearch[T]:
    def __init__(self, students: Iterable[T]):
        self.students = list(students)
        self.index = TextSearch(
            self.students,
            text=lambda row: " ".join(student_search_fields(row)),
            order=lambda row: normalized(student_search_fields(row)[0]),
        )

    def find(self, query: str) -> list[T]:
        return self.index.find(query)


def report_sort_value(row: Mapping[str, Any], field: str) -> str | int | float | None:
    if field == "full_name":
        return normalized(student_search_fields(row)[0])
    if field == "group":
        number = row.get("group_number", row.get("group"))
        return int(number) if str(number).isdecimal() else None
    if field == "topic":
        return normalized(row["topic"]) if row.get("topic") and row.get("lesson_id") else None
    if field == "lesson":
        starts_at = row.get("lesson_starts_at")
        if isinstance(starts_at, str):
            starts_at = datetime.fromisoformat(starts_at)
        return starts_at.timestamp() if isinstance(starts_at, datetime) else None
    if field == "grade":
        return row.get("grade")
    raise ValueError(f"Unknown report column: {field}")


def filter_report_rows[T: Mapping[str, Any]](
    rows: Iterable[T],
    *,
    query: str = "",
    group_id: int | None = None,
    status: str = "",
    lesson_id: int | None = None,
    sort: str = "",
    descending: bool = False,
) -> list[T]:
    """Keep record identity while sharing the report table's query in both front ends."""
    filtered = [
        row
        for row in rows
        if (group_id is None or row.get("group_id") == group_id)
        and (status != "graded" or row.get("grade") is not None)
        and (status != "pending" or row.get("grade") is None)
        and (lesson_id is None or row.get("lesson_id") == lesson_id)
    ]
    if query.strip():
        filtered = StudentSearch(filtered).find(query)
    if sort not in REPORT_SORT_FIELDS:
        return filtered
    present = []
    missing = []
    for row in filtered:
        key = report_sort_value(row, sort)
        if key is None:
            missing.append(row)
        else:
            present.append((key, row))
    return [
        row for _, row in sorted(present, key=lambda pair: pair[0], reverse=descending)
    ] + missing
