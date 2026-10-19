"""Typed file-transfer port. Views depend on this contract, not CSV implementations."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from attendance_lab.domain.models import AttendanceReport, Lesson, LogRow, Student


@dataclass(frozen=True, slots=True)
class ImportPreview:
    students: tuple[Student, ...]
    errors: tuple[str, ...]
    updates: int = 0


class CsvPort(Protocol):
    def read_csv(
        self, path: Path, encoding: str = "utf-8-sig", delimiter: str = ";"
    ) -> tuple[list[str], list[list[str]]]: ...
    def preview_students(
        self,
        headers: list[str],
        rows: list[list[str]],
        id_column: int | None,
        name_column: int | None,
        group_column: int | None,
    ) -> ImportPreview: ...
    def write_csv(
        self,
        path: Path,
        headers: Sequence[object],
        rows: Iterable[Sequence[object]],
        delimiter: str = ";",
        bom: bool = True,
    ) -> None: ...
    def export_report(
        self,
        path: Path,
        report: AttendanceReport,
        kind: str = "matrix",
        delimiter: str = ";",
        bom: bool = True,
    ) -> None: ...
    def export_log(
        self, path: Path, lesson: Lesson, rows: list[LogRow], delimiter: str = ";", bom: bool = True
    ) -> None: ...
