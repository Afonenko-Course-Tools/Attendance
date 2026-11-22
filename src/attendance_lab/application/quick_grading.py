"""Qt-free commands and JSON projections for the production assessment page."""

from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from attendance_lab.application.grading import GradingInputs
from attendance_lab.application.grading_preview import (
    CalculationDraft,
    GradingFitRequest,
    GradingPreviewRequest,
    GradingPreviewService,
    ReportCardDraft,
    preview_report_card,
)
from attendance_lab.application.quick_academic_common import lesson_record
from attendance_lab.application.quick_common import plain
from attendance_lab.application.text_search import TextSearch
from attendance_lab.domain.grading import (
    CRITERIA,
    CalculationParameters,
    FitBound,
    ReportCard,
    WrittenScore,
)
from attendance_lab.domain.models import AttendanceError

MUTATING_COMMANDS = frozenset(
    "grading." + name
    for name in (
        "save_report",
        "enable_reports",
        "add_work",
        "score",
        "paste_scores",
        "apply",
        "save_template",
        "export",
    )
)


def parameter_fields(parameters: CalculationParameters, works=()) -> dict:
    """The editor uses explicit fields; ordinal contributions retain their centre."""
    coefficients = [
        {
            "key": f"criterion:{i}",
            "title": criterion.title,
            "value": parameters.criterion_weights[i],
            "minimum": 0.1,
            "maximum": 20,
            "locked": False,
        }
        for i, criterion in enumerate(CRITERIA)
    ]
    for kind, values, centre in (
        ("three", parameters.three_values, 1),
        ("five", parameters.five_values, 2),
    ):
        coefficients.extend(
            {
                "key": f"{kind}:{i}",
                "title": f"Вклад {len(values)} уровней: {i + 1}",
                "value": value,
                "minimum": -1,
                "maximum": 1,
                "locked": False,
            }
            for i, value in enumerate(values)
            if i != centre
        )
    coefficients.extend(
        {
            "key": f"work:{work.id}",
            "title": "Вес: " + work.title,
            "value": dict(parameters.work_weights).get(work.id, work.weight),
            "minimum": 0.1,
            "maximum": 20,
            "locked": False,
        }
        for work in works
    )
    return {
        "version": parameters.version,
        "neutral": parameters.neutral,
        "spread": parameters.spread,
        "report_weight": parameters.report_weight,
        "final_offset": parameters.final_offset,
        "final_scale": parameters.final_scale,
        "coefficients": coefficients,
    }


def calculation_draft(values: dict) -> CalculationDraft:
    return CalculationDraft(
        **{
            key: _number(values[key])
            for key in ("neutral", "spread", "report_weight", "final_offset", "final_scale")
        },
        coefficients=tuple(
            (str(row["key"]), _number(row["value"])) for row in values.get("coefficients", [])
        ),
    )


def _number(value: Any) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError) as error:
        raise AttendanceError("Введите числовое значение.") from error


def _score(value: Any, variant: str | None = None) -> WrittenScore | float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    points = _number(value)
    return points if variant is None else WrittenScore(points, variant)


class GradingActions:
    """Owned by the service worker; all SQLite and file operations stay there.

    A result is JSON-safe. Drafts belong to the GUI coordinator; this adapter
    never retains a card or parameter draft between commands.
    """

    def __init__(self, service, files=None):
        self.service = service
        self.grading = service.grading
        self.previews = GradingPreviewService(self.grading)
        self.files = files
        self._requests: dict[int, GradingPreviewRequest] = {}
        self._transient: dict[int, dict] = {}
        self._edit_serial = 0
        self._topic_catalogs: dict[int, tuple] = {}
        self._topic_indexes: dict[int, TextSearch] = {}

    def snapshot(self, course_id: int, lesson_id: int | None = None) -> dict:
        with self.service.repository.transaction(write=False):
            grading = self.grading
            data = grading.view_data(course_id)
            students, works, parameters, scores = (
                data.students,
                data.works,
                data.parameters,
                data.scores,
            )
            lessons = [lesson_record(lesson) for lesson in data.lessons if not lesson.deleted]
            live_ids = {row["id"] for row in lessons}
            occurrences = {lesson.id: lesson for lesson in data.lessons}
            variants: dict[int, set[str]] = defaultdict(set)
            for key in scores:
                variants[key[1]].add(data.variants.get(key, ""))
            reports, score_rows = [], []
            for student in students:
                card = data.reports.get(student.student_id)
                occurrence = occurrences.get(card.lesson_id) if card else None
                report = (
                    asdict(card)
                    if card
                    else {
                        "course_id": course_id,
                        "student_id": student.student_id,
                        "lesson_id": lesson_id if lesson_id in live_ids else None,
                        "topic": "",
                        "written": False,
                        "levels": [criterion.steps // 2 for criterion in CRITERIA],
                    }
                )
                report["levels"] = list(report["levels"])
                reports.append(
                    {
                        "id": student.student_id,
                        "student_id": student.student_id,
                        "full_name": student.full_name,
                        "group": str(student.group_number or ""),
                        "group_id": student.group_id,
                        "group_number": student.group_number,
                        "record_book_number": student.record_book_number,
                        "specialty": student.specialty,
                        "profile": student.profile,
                        "subgroup": student.subgroup,
                        "lesson_id": card.lesson_id if card else None,
                        "topic": card.topic if card else "Не оценён",
                        "lesson": occurrence.label if occurrence else "",
                        "lesson_starts_at": occurrence.starts_at.isoformat()
                        if occurrence
                        else None,
                        "archived_lesson": lesson_record(occurrence)
                        if occurrence and occurrence.deleted
                        else None,
                        "grade": data.results[student.student_id].report,
                        "card": report,
                    }
                )
                score_rows.append(
                    {
                        "id": student.student_id,
                        "student_id": student.student_id,
                        "full_name": student.full_name,
                        "cells": [
                            {
                                "student_id": student.student_id,
                                "work_id": work.id,
                                "title": work.title,
                                "maximum": work.maximum,
                                "value": scores.get((student.student_id, work.id)),
                                "variant": data.variants.get((student.student_id, work.id)),
                                "legacy": (student.student_id, work.id) in scores
                                and (student.student_id, work.id) not in data.variants,
                                "text": ""
                                if (student.student_id, work.id) not in scores
                                else f"{scores[student.student_id, work.id]:g}",
                            }
                            for work in works
                        ],
                    }
                )
            initial = self._preview(
                self._requests.get(
                    course_id,
                    GradingPreviewRequest(
                        course_id, calculation_draft(parameter_fields(parameters, works))
                    ),
                ),
                inputs=data,
            )
            groups = {s.group_id: s.group_number for s in students if s.group_id is not None}
            return {
                "course_id": course_id,
                "reports_enabled": data.reports_enabled,
                "reports": reports,
                "scoreRows": score_rows,
                "student_ids": [student.student_id for student in students],
                "works": [asdict(work) for work in works],
                "variants": [
                    {
                        "work_id": work_id,
                        "variant": variant,
                        "label": variant or "Вариант не указан",
                    }
                    for work_id, values in sorted(variants.items())
                    for variant in sorted(values, key=lambda value: (not value, value))
                ],
                "lessons": lessons,
                "criteria": [
                    {
                        "index": i,
                        "key": c.key,
                        "title": c.title,
                        "written": c.written,
                        "steps": c.steps,
                        "labels": ["Хуже", "Нейтрально", "Лучше"]
                        if c.steps == 3
                        else [
                            "Значительно хуже",
                            "Хуже",
                            "Нейтрально",
                            "Лучше",
                            "Значительно лучше",
                        ],
                    }
                    for i, c in enumerate(CRITERIA)
                ],
                "parameters": parameter_fields(parameters, works),
                "parameter_fields": [
                    {"key": key, "title": title, "minimum": low, "maximum": high}
                    for key, title, low, high in (
                        ("neutral", "Нейтральная карточка", 0, 10),
                        ("spread", "Размах доклада", 0.1, 10),
                        ("report_weight", "Вес доклада", 0.1, 20),
                        ("final_offset", "Сдвиг итоговой шкалы", -5, 5),
                        ("final_scale", "Множитель итоговой шкалы", 0.1, 3),
                    )
                ],
                "metrics": [{"label": "Итог курса", "value": None}]
                + ([{"label": "Доклад", "value": "report"}] if data.reports_enabled else [])
                + [{"label": work.title, "value": work.id} for work in works],
                "slices": [{"label": "Весь курс", "group_id": None, "specialty": None}]
                + [
                    {"label": f"Группа {number}", "group_id": key, "specialty": None}
                    for key, number in sorted(groups.items())
                ]
                + [
                    {"label": "Специальность " + value, "group_id": None, "specialty": value}
                    for value in sorted({s.specialty for s in students})
                ],
                "templates": [{"name": name} for name in grading.templates(course_id)],
                "preview": initial,
                "changes": initial["changes"],
                **self._transient.get(course_id, {}),
            }

    def _preview(
        self,
        request: GradingPreviewRequest,
        fit: dict | None = None,
        *,
        inputs: GradingInputs | None = None,
    ) -> dict:
        if fit is None:
            result = self.previews.preview(request, inputs=inputs)
        else:
            bounds = tuple(
                FitBound(
                    str(row["key"]),
                    _number(row["minimum"]),
                    _number(row["maximum"]),
                    bool(row.get("locked", False)),
                )
                for row in fit.get("bounds", [])
            )
            result = self.previews.fit(
                GradingFitRequest(
                    request, bounds, _number(fit["target_mean"]), _number(fit["target_deviation"])
                )
            )
        return {
            "course_id": result.course_id,
            "revision": result.revision,
            "parameter_version": result.parameter_version,
            "parameters": parameter_fields(
                result.parameters,
                self.grading.works(request.course_id) if inputs is None else inputs.works,
            ),
            "baseline_statistics": plain(result.baseline_statistics),
            "statistics": plain(result.statistics),
            "variant_statistics": plain(result.variant_statistics),
            "fit_statistics": plain(result.fit_statistics),
            "changes": [
                {
                    "id": row.student_id,
                    "student_id": row.student_id,
                    "full_name": row.full_name,
                    "baseline": row.baseline.mark,
                    "candidate": row.candidate.mark,
                    "readiness": row.readiness,
                }
                for row in result.changes
            ],
            "histogram": [
                {"mark": mark, "baseline": current, "candidate": candidate}
                for mark, (current, candidate) in enumerate(
                    zip(result.baseline_statistics.counts, result.statistics.counts, strict=True)
                )
            ],
            "histogram_max": max(1, *result.baseline_statistics.counts, *result.statistics.counts),
        }

    def _request(self, payload: dict) -> GradingPreviewRequest:
        return GradingPreviewRequest(
            int(payload["course_id"]),
            calculation_draft(payload["parameters"]),
            int(payload.get("revision", 0)),
            payload.get("group_id"),
            payload.get("specialty"),
            payload.get("metric"),
            payload.get("variant"),
        )

    def execute(self, name: str, payload: dict) -> dict:
        """Every payload contains captured IDs; selection is never consulted here."""
        if not name.startswith("grading."):
            raise AttendanceError("Неизвестная команда оценивания.")
        course_id = int(payload["course_id"])
        # Validate course before even read-only helpers accept its identity.
        self.service.courses.get_course(course_id)
        command = name.removeprefix("grading.")
        details: dict = {}
        if command in ("preview", "fit"):
            request = self._request(payload)
            preview = self._preview(request, payload if command == "fit" else None)
            self._requests[course_id] = GradingPreviewRequest(
                course_id,
                calculation_draft(preview["parameters"]),
                request.revision,
                request.group_id,
                request.specialty,
                request.metric,
                request.variant,
            )
            if command == "fit":
                self._edit_parameters(course_id, preview["parameters"], request.revision, "fit")
            return {"preview": preview}
        if command == "topic_search":
            topics = self.grading.report_topic_catalog(course_id)
            if self._topic_catalogs.get(course_id) != topics:
                positions = {id(topic): i for i, topic in enumerate(topics)}
                self._topic_catalogs[course_id] = topics
                self._topic_indexes[course_id] = TextSearch(
                    topics,
                    text=lambda t: f"{t.title} {t.section} {t.source_number or ''}",
                    order=lambda t: positions[id(t)],
                )
            search = self._topic_indexes[course_id]
            result = {
                "topic_matches": [asdict(topic) for topic in search.find(payload.get("query", ""))],
                "query": payload.get("query", ""),
                "revision": payload.get("revision", 0),
                "topic_student_id": payload.get("student_id", ""),
                "topic_revision": payload.get("revision", 0),
            }
            self._transient.setdefault(course_id, {}).update(result)
            return result
        if command == "report_preview":
            draft = self._report_draft(course_id, payload)
            self._check_student(course_id, draft.student_id)
            result = preview_report_card(draft, self.grading.parameters(course_id))
            projection = {
                "report_preview": {**asdict(result), "revision": payload.get("revision", 0)},
                "revision": payload.get("revision", 0),
            }
            self._transient.setdefault(course_id, {}).update(projection)
            return projection
        if command == "save_report":
            draft = self._report_draft(course_id, payload)
            if draft.lesson_id is None:
                raise AttendanceError("Выберите конкретное занятие сдачи.")
            self.grading.save_report(
                ReportCard(
                    course_id,
                    draft.student_id,
                    draft.lesson_id,
                    draft.topic,
                    draft.written,
                    draft.levels,
                )
            )
            details["student_id"] = draft.student_id
        elif command == "enable_reports":
            course = self.service.courses.get_course(course_id)
            self.service.courses.save_course(
                course.discipline_id,
                course.academic_year_id,
                course.semester,
                course.study_course,
                course.kind,
                course.duration,
                course.name,
                reports_enabled=True,
                course_id=course.id,
            )
        elif command == "add_work":
            work = self.grading.add_work(
                course_id,
                str(payload["title"]),
                _number(payload.get("maximum", 100)),
                _number(payload.get("weight", 1)),
            )
            details["work_id"] = work.id
        elif command == "score":
            student_id, work_id = str(payload["student_id"]), int(payload["work_id"])
            self.grading.save_scores(
                course_id,
                {(student_id, work_id): _score(payload.get("value"), payload.get("variant"))},
            )
            details.update(student_id=student_id, work_id=work_id)
        elif command == "paste_scores":
            self._paste(course_id, payload)
        elif command in ("apply", "save_template"):
            with self.service.repository.transaction():
                current = self.grading.parameters(course_id)
                if payload.get("parameter_version", current.version) != current.version:
                    raise AttendanceError("Применённый расчёт изменился. Обновите параметры.")
                parameters = self.previews.candidate(
                    course_id, calculation_draft(payload["parameters"])
                )
                if command == "apply":
                    applied = self.grading.apply_parameters(course_id, parameters)
                    details["parameter_version"] = applied.version
                else:
                    self.grading.save_template(str(payload["name"]), parameters)
        elif command == "load_template":
            templates = self.grading.templates(course_id)
            if payload.get("name") not in templates:
                raise AttendanceError("Вариант расчёта больше недоступен.")
            parameters = parameter_fields(templates[payload["name"]], self.grading.works(course_id))
            parameters["version"] = self.grading.parameters(course_id).version
            self._edit_parameters(
                course_id, parameters, int(payload.get("revision", 0)), "template"
            )
            return {"parameters": parameters}
        elif command == "export":
            if self.files is None:
                raise AttendanceError("Файловый адаптер не подключён.")
            raw = str(payload["path"])
            parsed = urlparse(raw)
            if parsed.scheme and parsed.scheme != "file":
                raise AttendanceError("Выберите локальный файл экспорта.")
            path = Path(unquote(parsed.path) if parsed.scheme == "file" else raw)
            if not path.name:
                raise AttendanceError("Выберите файл экспорта.")
            headers, rows = self.grading.export_projection(
                course_id, observed_only=bool(payload.get("observed_only", False))
            )
            self.files.export_xlsx(path, headers, rows)
            return {"exported": str(path)}
        else:
            raise AttendanceError("Неизвестная команда оценивания.")
        return {"course_id": course_id, "saved": command, **details}

    def _edit_parameters(self, course_id: int, parameters: dict, revision: int, kind: str) -> None:
        self._edit_serial += 1
        self._transient.setdefault(course_id, {}).update(
            edit_parameters=parameters,
            edit_token=self._edit_serial,
            edit_revision=revision,
            edit_kind=kind,
        )

    @staticmethod
    def _report_draft(course_id: int, payload: dict) -> ReportCardDraft:
        card = payload.get("card", payload)
        levels = tuple(card.get("levels", [c.steps // 2 for c in CRITERIA]))
        return ReportCardDraft(
            course_id,
            str(card["student_id"]),
            card.get("lesson_id"),
            str(card.get("topic", "")),
            bool(card.get("written", False)),
            levels,
        )

    def _check_student(self, course_id: int, student_id: str) -> None:
        if student_id not in {student.student_id for student in self.grading.students(course_id)}:
            raise AttendanceError("Студент больше не входит в состав курса.")

    def _paste(self, course_id: int, payload: dict) -> None:
        students = tuple(str(value) for value in payload["student_ids"])
        works = tuple(int(value) for value in payload["work_ids"])
        if len(set(students)) != len(students) or len(set(works)) != len(works):
            raise AttendanceError("Повторяется адресат вставки.")
        row, column = int(payload.get("row", 0)), int(payload.get("column", 0))
        changes = {}
        for dr, line in enumerate(str(payload["text"]).splitlines()):
            for dc, value in enumerate(line.split("\t")):
                target_row, target_column = row + dr, column + dc
                if not (0 <= target_row < len(students) and 0 <= target_column < len(works)):
                    raise AttendanceError("Вставка выходит за область исходных баллов.")
                variant, separator, points = value.partition(":")
                if separator and not points.strip():
                    raise AttendanceError("Укажите баллы после варианта контрольной работы.")
                changes[students[target_row], works[target_column]] = (
                    _score(points, variant) if separator else _score(value, payload.get("variant"))
                )
        self.grading.save_scores(course_id, changes)
