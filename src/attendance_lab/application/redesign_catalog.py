"""Exact catalog resolution and atomic official-list imports, without SQL."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from attendance_lab.application.redesign_ports import RedesignStore
from attendance_lab.domain.models import AttendanceError, Student
from attendance_lab.domain.redesign import (
    AcademicGroup,
    AcademicYear,
    ImportPlan,
    ImportRow,
    Profile,
    Specialty,
    StudentContext,
    display_name,
    name_key,
)
from attendance_lab.domain.rules import (
    required_text,
)

if TYPE_CHECKING:
    from attendance_lab.application.service import AttendanceService

IMPORT_HEADERS = (
    "Внутренний № по списку",
    "ФИО",
    "Специальность",
    "Профилизация",
    "Курс",
    "Группа",
    "Номер зачётки",
)


def positive(value: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise AttendanceError(f"{label}: необходимо положительное целое число.")
    return value


def book_number(value: str, *, unresolved: bool = False) -> str:
    value = value.strip()
    if (not unresolved or value) and re.fullmatch("[0-9]+", value) is None:
        raise AttendanceError("Номер зачётки должен состоять из ASCII-цифр.")
    return value


class CatalogUseCases:
    def __init__(self, service: AttendanceService) -> None:
        self.service = service
        self.store = cast(RedesignStore, service.repository)

    def list_years(self) -> list[AcademicYear]:
        return self.store.rd_years()

    def save_year(self, start_year: int) -> AcademicYear:
        positive(start_year, "Учебный год")
        with self.service.repository.transaction():
            return self.store.rd_save_year(start_year)

    def list_specialties(self) -> list[Specialty]:
        return self.store.rd_specialties()

    def list_profiles(self, specialty_id: int | None = None) -> list[Profile]:
        return [
            p
            for p in self.store.rd_profiles()
            if specialty_id is None or p.specialty_id == specialty_id
        ]

    def list_groups(self, academic_year_id: int | None = None) -> list[AcademicGroup]:
        return [
            g
            for g in self.store.rd_groups()
            if academic_year_id is None or g.academic_year_id == academic_year_id
        ]

    def list_student_contexts(
        self,
        academic_year_id: int | None = None,
        discipline_id: int | None = None,
        semester: int | None = None,
    ) -> list[StudentContext]:
        contexts = self.store.rd_contexts(academic_year_id)
        if discipline_id is None:
            return contexts
        rosters = [
            r
            for r in self.store.rd_rosters()
            if r.discipline_id == discipline_id
            and (academic_year_id is None or r.academic_year_id == academic_year_id)
            and (semester is None or r.semester == semester)
        ]
        result = []
        for context in contexts:
            labels = [
                r.name
                for r in rosters
                if r.mode == "students" and context.student_id in r.student_ids
            ]
            result.append(replace(context, subgroup=", ".join(labels)))
        return result

    def add_specialty_alias(self, specialty_id: int, name: str) -> None:
        if not any(s.id == specialty_id for s in self.list_specialties()):
            raise AttendanceError("Специальность не найдена.")
        with self.service.repository.transaction():
            self.store.rd_specialty_alias(
                specialty_id, required_text(display_name(name), "Название")
            )

    def add_profile_alias(self, profile_id: int, name: str) -> None:
        with self.service.repository.transaction():
            self.store.rd_profile_alias(profile_id, required_text(display_name(name), "Название"))

    def rename_specialty(self, specialty_id: int, name: str) -> Specialty:
        if not any(s.id == specialty_id for s in self.list_specialties()):
            raise AttendanceError("Специальность не найдена.")
        with self.service.repository.transaction():
            return self.store.rd_save_specialty(
                required_text(display_name(name), "Название"), specialty_id
            )

    def rename_profile(self, profile_id: int, name: str) -> Profile:
        profile = next((p for p in self.list_profiles() if p.id == profile_id), None)
        if profile is None:
            raise AttendanceError("Профилизация не найдена.")
        with self.service.repository.transaction():
            return self.store.rd_save_profile(
                profile.specialty_id, required_text(display_name(name), "Название"), profile_id
            )

    def save_group(
        self,
        academic_year_id: int,
        specialty_id: int,
        course: int,
        number: int,
        variant_key: str = "",
        group_id: int | None = None,
    ) -> AcademicGroup:
        positive(course, "Курс")
        positive(number, "Группа")
        if academic_year_id not in {y.id for y in self.list_years()} or specialty_id not in {
            s.id for s in self.list_specialties()
        }:
            raise AttendanceError("Выберите существующие учебный год и специальность.")
        if group_id is not None and group_id not in {g.id for g in self.list_groups()}:
            raise AttendanceError("Группа не найдена.")
        with self.service.repository.transaction():
            return self.store.rd_save_group(
                AcademicGroup(
                    group_id or 0,
                    academic_year_id,
                    specialty_id,
                    course,
                    number,
                    display_name(variant_key),
                )
            )

    def _parse_rows(self, headers, rows):
        errors = []
        parsed = []
        seen = {}
        if tuple(display_name(h).lstrip("\ufeff") for h in headers) != IMPORT_HEADERS:
            errors.append("Необходимы семь колонок официального списка в указанном порядке.")
        if not rows:
            errors.append("В CSV нет строк студентов.")
        for source_row, values in enumerate(rows, 2):
            if len(values) != 7:
                errors.append(f"Строка {source_row}: необходимо семь полей.")
                continue
            try:
                ordinal, course, group = (
                    positive(int(values[i]), label)
                    for i, label in ((0, "№ по списку"), (4, "Курс"), (5, "Группа"))
                )
                name = required_text(display_name(values[1]), "ФИО")
                specialty = required_text(display_name(values[2]), "Специальность")
                book = book_number(values[6])
                if book in seen:
                    raise AttendanceError(
                        f"Повтор номера зачётки: строки {seen[book]} и {source_row}."
                    )
                seen[book] = source_row
                parsed.append(
                    ImportRow(
                        source_row,
                        ordinal,
                        name,
                        specialty,
                        display_name(values[3]),
                        course,
                        group,
                        book,
                    )
                )
            except (AttendanceError, ValueError) as exc:
                errors.append(f"Строка {source_row}: {exc}")
        return parsed, errors

    def _resolve_preview(self, rows, year, specialty_aliases, profile_aliases, group_choices):
        errors = []
        changes = []
        specs = {s.id: s for s in self.list_specialties()}
        profiles = {p.id: p for p in self.list_profiles()}
        aliases = {name_key(name): sid for name, sid in specialty_aliases.items()}
        paliases = {(sid, name_key(name)): pid for (sid, name), pid in profile_aliases.items()}
        for name, sid in specialty_aliases.items():
            current = self.store.rd_resolve_specialty(name_key(name))
            if sid not in specs or (current is not None and current != sid):
                errors.append(f"Конфликт псевдонима специальности: {name}.")
        for (sid, name), pid in profile_aliases.items():
            current = self.store.rd_resolve_profile(sid, name_key(name))
            if (
                pid not in profiles
                or profiles[pid].specialty_id != sid
                or (current is not None and current != pid)
            ):
                errors.append(f"Конфликт псевдонима профилизации: {name}.")
        year_id = next((y.id for y in self.list_years() if y.start_year == year), None)
        groups = self.list_groups(year_id) if year_id else []
        contexts = {
            c.record_book_number: c
            for c in self.list_student_contexts(year_id)
            if c.record_book_number
        }
        new_specs = set()
        new_profiles = set()
        new_groups = set()
        unchanged = 0
        for row in rows:
            sid = aliases.get(
                name_key(row.specialty), self.store.rd_resolve_specialty(name_key(row.specialty))
            )
            if sid is None:
                new_specs.add(row.specialty)
            if row.profile and (
                sid is None
                or paliases.get(
                    (sid, name_key(row.profile)),
                    self.store.rd_resolve_profile(sid, name_key(row.profile)),
                )
                is None
            ):
                new_profiles.add((sid or name_key(row.specialty), row.profile))
            matches = [
                g
                for g in groups
                if g.specialty_id == sid and g.course == row.course and g.number == row.group_number
            ]
            choice = group_choices.get(row.source_row)
            if choice is not None and choice not in {g.id for g in matches}:
                errors.append(
                    f"Строка {row.source_row}: выбранная группа не соответствует контексту CSV."
                )
            elif len(matches) > 1 and choice is None:
                errors.append(f"Строка {row.source_row}: неоднозначная группа; выберите уточнение.")
            elif not matches:
                new_groups.add((sid or name_key(row.specialty), row.course, row.group_number))
            old = contexts.get(row.record_book_number)
            if old is None:
                changes.append(f"Новый студент: {row.full_name}, зачётка {row.record_book_number}.")
            else:
                changed = False
                if old.full_name != row.full_name:
                    changes.append(f"Исправление ФИО: {old.full_name} → {row.full_name}.")
                    changed = True
                selected = choice or (matches[0].id if len(matches) == 1 else None)
                pid = (
                    paliases.get(
                        (sid, name_key(row.profile)),
                        self.store.rd_resolve_profile(sid, name_key(row.profile)),
                    )
                    if sid and row.profile
                    else None
                )
                canonical_profile = profiles[pid].name if pid in profiles else row.profile
                if (
                    old.academic_year_id != year_id
                    or old.group_id != selected
                    or old.profile != canonical_profile
                ):
                    changes.append(f"Изменение зачисления: {row.full_name}.")
                    changed = True
                if not changed:
                    unchanged += 1
        changes = (
            [
                f"Учебный год {year}/{str(year + 1)[-2:]}.",
                f"Новые специальности: {len(new_specs)}; профилизации: {len(new_profiles)}; группы: {len(new_groups)}.",
            ]
            + changes
            + [f"Без изменений: {unchanged}."]
        )
        return errors, changes

    def preview_import(
        self,
        headers: list[str],
        rows: list[list[str]],
        academic_year_start: int,
        *,
        specialty_aliases: dict[str, int] | None = None,
        profile_aliases: dict[tuple[int, str], int] | None = None,
        group_choices: dict[int, int] | None = None,
        file_hash: str = "",
    ) -> ImportPlan:
        specialty_aliases = specialty_aliases or {}
        profile_aliases = profile_aliases or {}
        group_choices = group_choices or {}
        parsed, errors = self._parse_rows(headers, rows)
        try:
            positive(academic_year_start, "Учебный год")
        except AttendanceError as exc:
            errors.append(str(exc))
        with self.service.repository.transaction(write=False):
            conflicts, changes = self._resolve_preview(
                parsed, academic_year_start, specialty_aliases, profile_aliases, group_choices
            )
            errors.extend(conflicts)
            revision = self.store.rd_revision()
        digest = (
            file_hash
            or hashlib.sha256(
                json.dumps([asdict(r) for r in parsed], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
        )
        return ImportPlan(
            tuple(parsed),
            tuple(errors),
            tuple(changes),
            revision,
            academic_year_start,
            digest,
            tuple(sorted(specialty_aliases.items())),
            tuple(sorted((sid, name, pid) for (sid, name), pid in profile_aliases.items())),
            tuple(sorted(group_choices.items())),
        )

    def apply_import(self, plan: ImportPlan) -> None:
        if plan.errors:
            raise AttendanceError("Импорт содержит ошибки: " + "; ".join(plan.errors))
        with self.service.repository.transaction():
            if plan.revision != self.store.rd_revision():
                raise AttendanceError(
                    "Справочник изменился после предпросмотра. Повторите предпросмотр."
                )
            rows = [
                [
                    str(r.ordinal),
                    r.full_name,
                    r.specialty,
                    r.profile,
                    str(r.course),
                    str(r.group_number),
                    r.record_book_number,
                ]
                for r in plan.rows
            ]
            # Validate public immutable plans too; callers cannot bypass parsing by constructing a DTO.
            parsed, errors = self._parse_rows(list(IMPORT_HEADERS), rows)
            aliases = dict(plan.specialty_aliases)
            paliases = {(sid, name): pid for sid, name, pid in plan.profile_aliases}
            choices = dict(plan.group_choices)
            conflicts, _ = self._resolve_preview(
                plan.rows, plan.academic_year_start, aliases, paliases, choices
            )
            if errors or conflicts:
                raise AttendanceError("; ".join(errors + conflicts))
            positive(plan.academic_year_start, "Учебный год")
            year = self.store.rd_save_year(plan.academic_year_start)
            for name, sid in plan.specialty_aliases:
                self.add_specialty_alias(sid, name)
            for _sid, name, pid in plan.profile_aliases:
                self.add_profile_alias(pid, name)
            student_ids = []
            for row in plan.rows:
                sid = self.store.rd_resolve_specialty(name_key(row.specialty))
                if sid is None:
                    sid = self.store.rd_save_specialty(row.specialty).id
                pid = None
                if row.profile:
                    pid = self.store.rd_resolve_profile(sid, name_key(row.profile))
                    if pid is None:
                        pid = self.store.rd_save_profile(sid, row.profile).id
                groups = [
                    g
                    for g in self.list_groups(year.id)
                    if g.specialty_id == sid
                    and g.course == row.course
                    and g.number == row.group_number
                ]
                group = (
                    next((g for g in groups if g.id == choices.get(row.source_row)), None)
                    if row.source_row in choices
                    else (groups[0] if groups else None)
                )
                if group is None:
                    group = self.save_group(year.id, sid, row.course, row.group_number)
                existing = next(
                    (
                        c
                        for c in self.store.rd_contexts()
                        if c.record_book_number == row.record_book_number
                    ),
                    None,
                )
                student_id = existing.student_id if existing else str(uuid4())
                self.service.repository.put_student(
                    Student(student_id, row.full_name, str(group.number))
                )
                self.store.rd_save_book(student_id, row.record_book_number)
                self.store.rd_membership(student_id, year.id, group.id, pid)
                student_ids.append(student_id)
            self.store.rd_import_batch(year.id, plan, student_ids)

    def save_student_context(
        self,
        student_id: str,
        full_name: str,
        record_book_number: str,
        academic_year_id: int | None = None,
        group_id: int | None = None,
        profile_id: int | None = None,
    ) -> StudentContext:
        student = self.service._student(student_id)
        number = book_number(record_book_number, unresolved=True)
        name = required_text(display_name(full_name), "ФИО")
        with self.service.repository.transaction():
            group = next((g for g in self.list_groups() if g.id == group_id), None)
            if (group_id is None) != (academic_year_id is None):
                raise AttendanceError("Укажите год и группу вместе.")
            if group_id is not None:
                if group is None or group.academic_year_id != academic_year_id:
                    raise AttendanceError("Группа относится к другому году.")
                if profile_id is not None and not any(
                    p.id == profile_id and p.specialty_id == group.specialty_id
                    for p in self.list_profiles()
                ):
                    raise AttendanceError("Профилизация относится к другой специальности.")
            self.service.repository.put_student(
                Student(student_id, name, str(group.number) if group else student.group)
            )
            self.store.rd_save_book(student_id, number)
            if group is not None:
                self.store.rd_membership(student_id, group.academic_year_id, group.id, profile_id)
        return next(
            c for c in self.list_student_contexts(academic_year_id) if c.student_id == student_id
        )
