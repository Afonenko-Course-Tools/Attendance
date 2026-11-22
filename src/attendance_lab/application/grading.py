"""Course-wide assessment use cases shared by widgets, statistics and export."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from statistics import fmean, pstdev
from types import MappingProxyType

from attendance_lab.application.lesson_choices import LessonChoice
from attendance_lab.application.report_topics import ReportTopic
from attendance_lab.domain.grading import (
    MINIMUM_VARIANT_FIT_SIZE,
    CalculationParameters,
    GradeResult,
    ReportCard,
    VariantBalance,
    WrittenScore,
    WrittenWork,
    calculate,
    distribution,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import EXCUSED_ABSENCE_REASON, StudentContext, name_key


@dataclass(frozen=True, slots=True)
class VariantStatistics:
    work_id: int
    work_title: str
    variant: str | None
    n: int
    mean: float
    deviation: float
    fit_n: int
    fit_eligible: bool


@dataclass(frozen=True, slots=True)
class GradingInputs:
    students: tuple[StudentContext, ...]
    works: tuple[WrittenWork, ...]
    parameters: CalculationParameters
    reports_enabled: bool
    scores: Mapping[tuple[str, int], float]
    reports: Mapping[str, ReportCard]
    variants: Mapping[tuple[str, int], str]

    def evaluator(self):
        observations = _observations(self.students, self.works, self.scores, self.reports)
        return lambda parameters: tuple(
            calculate(student_id, self.works, marks, card, self.reports_enabled, parameters)
            for student_id, marks, card in observations
        )

    def selected_students(self, group_id, specialty):
        return {
            student.student_id
            for student in self.students
            if (group_id is None or student.group_id == group_id)
            and (specialty is None or student.specialty == specialty)
        }

    def statistics(
        self, results, parameters, *, group_id=None, specialty=None, metric=None, variant=None
    ):
        ids = self.selected_students(group_id, specialty)
        if variant is not None and not isinstance(metric, int):
            raise AttendanceError("Выберите контрольную работу для фильтра по варианту.")
        if metric == "report":
            results = tuple(replace(result, final=result.report) for result in results)
        elif isinstance(metric, int):
            work = next((work for work in self.works if work.id == metric), None)
            if work is None:
                raise AttendanceError("Работа для статистики не найдена.")
            if variant is not None:
                ids = {
                    student_id
                    for student_id in ids
                    if (student_id, work.id) in self.scores
                    and self.variants.get((student_id, work.id), "") == variant
                }
            normalized = replace(parameters, final_scale=1.0, final_offset=0.0)
            results = tuple(
                calculate(student_id, (work,), marks, None, False, normalized)
                for student_id, marks, _ in _observations(self.students, (work,), self.scores, {})
            )
        return distribution(tuple(result for result in results if result.student_id in ids))

    def variant_statistics(
        self, results, balance: VariantBalance, *, group_id=None, specialty=None, metric=None
    ) -> tuple[VariantStatistics, ...]:
        if metric == "report":
            return ()
        selected = self.selected_students(group_id, specialty)
        complete = {result.student_id for result in results if result.final is not None}
        works = {
            work.id: work for work in self.works if not isinstance(metric, int) or work.id == metric
        }
        scores: dict[tuple[int, str | None], list[float]] = defaultdict(list)
        fit_counts: dict[tuple[int, str | None], int] = defaultdict(int)
        for (student_id, work_id), score in self.scores.items():
            if work_id not in works:
                continue
            variant = self.variants.get((student_id, work_id))
            if student_id in selected:
                scores[work_id, variant].append(score / works[work_id].maximum * 10)
            if student_id in complete:
                fit_counts[work_id, variant] += 1
        return tuple(
            VariantStatistics(
                work_id,
                works[work_id].title,
                variant,
                len(values),
                fmean(values),
                pstdev(values),
                fit_counts[work_id, variant],
                variant is not None
                and work_id in balance.work_ids
                and fit_counts[work_id, variant] >= MINIMUM_VARIANT_FIT_SIZE,
            )
            for (work_id, variant), values in sorted(
                scores.items(),
                key=lambda entry: (entry[0][0], entry[0][1] is None, entry[0][1] or ""),
            )
        )


@dataclass(frozen=True, slots=True)
class GradingViewData(GradingInputs):
    results: Mapping[str, GradeResult]
    lessons: tuple[LessonChoice, ...]


def _observations(students, works, scores, reports):
    return tuple(
        (
            student.student_id,
            {
                work.id: scores[(student.student_id, work.id)]
                for work in works
                if (student.student_id, work.id) in scores
            },
            reports.get(student.student_id),
        )
        for student in students
    )


class GradingService:
    def __init__(self, service):
        self.service = service
        self.store = service.repository

    def students(self, course_id):
        return tuple(self.service.courses.list_students(course_id))

    def works(self, course_id):
        self.store.gr_course(course_id)
        return self.store.gr_works(course_id)

    def lessons(self, course_id, *, include_deleted=False):
        return self.store.gr_lessons(course_id, include_deleted=include_deleted)

    def lesson_choices(self, course_id, *, include_deleted=False) -> tuple[LessonChoice, ...]:
        return self.store.gr_lesson_choices(course_id, include_deleted=include_deleted)

    def parameters(self, course_id):
        self.store.gr_course(course_id)
        return self.store.gr_parameters(course_id)

    def report(self, course_id, student_id):
        return next(
            (r for r in self.store.gr_reports(course_id) if r.student_id == student_id), None
        )

    def report_index(self, course_id) -> dict[str, ReportCard]:
        return {report.student_id: report for report in self.store.gr_reports(course_id)}

    def scores(self, course_id):
        self.store.gr_course(course_id)
        return self.store.gr_scores(course_id)

    def variants(self, course_id) -> dict[tuple[str, int], str]:
        self.store.gr_course(course_id)
        return self.store.gr_variants(course_id)

    def reports_enabled(self, course_id):
        return self.store.gr_course(course_id)[1]

    def inputs(self, course_id) -> GradingInputs:
        with self.store.transaction(write=False):
            _, enabled = self.store.gr_course(course_id)
            return GradingInputs(
                self.students(course_id),
                self.store.gr_works(course_id),
                self.store.gr_parameters(course_id),
                enabled,
                MappingProxyType(self.store.gr_scores(course_id)),
                MappingProxyType(self.report_index(course_id)),
                MappingProxyType(self.store.gr_variants(course_id)),
            )

    def view_data(self, course_id) -> GradingViewData:
        """One coherent set of inputs for the grade lists and lesson selectors."""
        with self.store.transaction(write=False):
            inputs = self.inputs(course_id)
            results = {
                result.student_id: result for result in inputs.evaluator()(inputs.parameters)
            }
            return GradingViewData(
                inputs.students,
                inputs.works,
                inputs.parameters,
                inputs.reports_enabled,
                inputs.scores,
                inputs.reports,
                inputs.variants,
                MappingProxyType(results),
                self.lesson_choices(course_id, include_deleted=True),
            )

    def report_topics(self, course_id):
        return [topic.title for topic in self.report_topic_catalog(course_id)]

    def report_topic_catalog(self, course_id):
        course = self.service.courses.get_course(course_id)
        discipline = next(
            discipline
            for discipline in self.service.list_disciplines()
            if discipline.id == course.discipline_id
        )
        topics = {}
        for catalog in self.service.report_topic_catalogs:
            if name_key(discipline.name) in {name_key(name) for name in catalog.discipline_names}:
                topics.update((name_key(topic.title), topic) for topic in catalog.topics)
        for report in sorted(self.store.gr_reports(course_id), key=lambda report: report.topic):
            topics.setdefault(name_key(report.topic), ReportTopic(report.topic))
        return tuple(topics.values())

    def save_report(self, card):
        with self.store.transaction():
            _, enabled = self.store.gr_course(card.course_id)
            if not enabled:
                raise AttendanceError("Доклады для этого курса не включены.")
            students = self.students(card.course_id)
            student = next((s for s in students if s.student_id == card.student_id), None)
            if student is None or student.group_id is None:
                raise AttendanceError(
                    "Студент должен входить в состав курса и академическую группу."
                )
            reports = self.report_index(card.course_id)
            saved = reports.get(card.student_id)
            include_deleted = saved is not None and saved.lesson_id == card.lesson_id
            allowed = {
                lesson.id
                for lesson in self.lesson_choices(card.course_id, include_deleted=include_deleted)
            }
            if card.lesson_id not in allowed:
                raise AttendanceError("Выберите неудалённое занятие сдачи этого курса.")
            classmates = {s.student_id: s for s in students if s.group_id == student.group_id}
            duplicate = next(
                (
                    r
                    for r in reports.values()
                    if r.student_id != card.student_id
                    and r.student_id in classmates
                    and name_key(r.topic) == name_key(card.topic)
                ),
                None,
            )
            if duplicate:
                owner = classmates[duplicate.student_id]
                raise AttendanceError(
                    f"Тема уже выбрана: {owner.full_name}, группа {owner.group_number}."
                )
            self.store.gr_save_report(card, student.group_id)

    def add_work(self, course_id, title, maximum=100.0, weight=1.0):
        WrittenWork(0, course_id, title, maximum, weight)
        with self.store.transaction():
            self.store.gr_course(course_id)
            return self.store.gr_add_work(course_id, title.strip(), maximum, weight)

    def save_scores(
        self, course_id, changes: Mapping[tuple[str, int], WrittenScore | float | None]
    ):
        """Validate all stable targets before writing; numeric edits keep their saved variant."""
        with self.store.transaction():
            students = {s.student_id for s in self.students(course_id)}
            works = {w.id: w for w in self.works(course_id)}
            variants = self.variants(course_id)
            validated = {}
            for (student_id, work_id), value in changes.items():
                target = student_id, work_id
                if student_id not in students or work_id not in works:
                    raise AttendanceError("Ячейка не относится к составу и работам курса.")
                if value is not None and not isinstance(value, WrittenScore):
                    value = WrittenScore(value, variants.get(target, ""))
                if value is not None and value.score > works[work_id].maximum:
                    raise AttendanceError("Баллы выходят за исходную шкалу работы.")
                validated[target] = value
            for (student_id, work_id), score in validated.items():
                self.store.gr_save_score(student_id, work_id, score)

    def evaluator(self, course_id):
        return self.inputs(course_id).evaluator()

    def results(self, course_id, parameters=None):
        inputs = self.inputs(course_id)
        return inputs.evaluator()(parameters or inputs.parameters)

    def statistics(
        self,
        course_id,
        parameters=None,
        *,
        group_id=None,
        specialty=None,
        metric=None,
        variant=None,
    ):
        inputs = self.inputs(course_id)
        params = parameters or inputs.parameters
        return inputs.statistics(
            inputs.evaluator()(params),
            params,
            group_id=group_id,
            specialty=specialty,
            metric=metric,
            variant=variant,
        )

    def apply_parameters(self, course_id, parameters):
        with self.store.transaction():
            self.store.gr_course(course_id)
            # Version one denotes the built-in defaults; each saved application is immutable.
            unknown = set(dict(parameters.work_weights)) - {w.id for w in self.works(course_id)}
            if unknown:
                raise AttendanceError(
                    "Вариант содержит веса работ другого курса. Сопоставьте работы явно."
                )
            applied = replace(parameters, version=self.store.gr_next_version(course_id))
            self.store.gr_apply_parameters(course_id, applied)
            return applied

    def save_template(self, name, parameters):
        if not name.strip():
            raise AttendanceError("Укажите название варианта расчёта.")
        with self.store.transaction():
            work_ids = set(dict(parameters.work_weights))
            all_works = tuple(
                w
                for c in self.service.courses.list_courses()
                for w in self.works(c.id)
                if w.id in work_ids
            )
            self.store.gr_save_template(name.strip(), parameters, all_works)

    def templates(self, course_id=None):
        return self.store.gr_templates(self.works(course_id) if course_id is not None else None)

    def export_projection(self, course_id, *, observed_only=False):
        """Flat journal for enrollment, or explicitly selected observed attendance rows.

        Observed rows are a display population, not an enrollment operation. Their
        actual attendance remains exportable without assigning compulsory works or
        inventing grades for students outside the course's expected membership.
        """
        students = (
            self.service.courses.list_observed_students(course_id)
            if observed_only
            else self.students(course_id)
        )
        inputs = self.inputs(course_id)
        works, scores, cards, variants = (
            inputs.works,
            inputs.scores,
            inputs.reports,
            inputs.variants,
        )
        lessons = dict(self.lessons(course_id, include_deleted=True))
        results = {r.student_id: r for r in inputs.evaluator()(inputs.parameters)}
        columns = self.store.gr_attendance_columns(course_id)
        attendance_labels = self.store.gr_attendance_labels(course_id)
        topics = dict((number, title) for number, title, _, _ in columns)
        evidence = {
            pair_id: self.service.redesign.pair_evidence(pair_id, include_excused=True)
            for _, _, _, pair_id in columns
        }
        headers = ["ФИО", "№ зачётки", "Группа", "Специальность", "Профилизация", "Подгруппа"]
        headers += [f"Тема {n}: {title} — даты посещения" for n, title in topics.items()]
        headers += [
            "Тема доклада",
            "Занятие сдачи: дата, время, группа",
            "Письменная часть",
            "Оценка доклада /10",
        ]
        for work in works:
            headers += [f"{work.title} /{work.maximum:g}", f"{work.title} — Вариант"]
        headers += ["Итог /10", "Полнота", "Версия расчёта"]
        rows = []
        for student in students:
            result = results.get(student.student_id)
            card = cards.get(student.student_id)
            row = [
                student.full_name,
                student.record_book_number,
                str(student.group_number or ""),
                student.specialty,
                student.profile,
                student.subgroup,
            ]
            for number in topics:
                events = set()
                for n, _, _, pair in columns:
                    if n != number:
                        continue
                    grounds = [
                        e for e in evidence[pair] if e.active and e.student_id == student.student_id
                    ]
                    if grounds:
                        presence = [e for e in grounds if e.kind != "excused"]
                        if presence:
                            actual = [e.at for e in presence if e.kind != "manual"]
                            first = min(actual or [e.at for e in presence])
                            kind = "Предъявление" if actual else "Подтверждено вручную"
                            events.add(f"{kind}: {first.isoformat()} · {attendance_labels[pair]}")
                        else:
                            events.add(f"{EXCUSED_ABSENCE_REASON} · {attendance_labels[pair]}")
                row.append("; ".join(sorted(events)))
            row += [
                card.topic if card else "",
                lessons.get(card.lesson_id, "") if card else "",
                ("Да" if card.written else "Нет") if card else "",
                result.report if result is not None else None,
            ]
            for work in works:
                key = student.student_id, work.id
                row += [scores.get(key), variants.get(key, "")]
            row += [
                result.mark if result is not None else None,
                "Состав не назначен"
                if result is None
                else (
                    "Не хватает: " + ", ".join(result.missing)
                    if result.missing
                    else ("Полный" if result.final is not None else "Оценивание не включено")
                ),
                result.parameter_version if result is not None else None,
            ]
            rows.append(row)
        return headers, rows
