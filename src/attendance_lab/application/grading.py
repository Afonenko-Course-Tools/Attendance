"""Course-wide assessment use cases shared by widgets, statistics and export."""

from dataclasses import replace
from math import isfinite

from attendance_lab.domain.grading import (
    WrittenWork,
    calculate,
    distribution,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import name_key


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

    def parameters(self, course_id):
        self.store.gr_course(course_id)
        return self.store.gr_parameters(course_id)

    def report(self, course_id, student_id):
        return next(
            (r for r in self.store.gr_reports(course_id) if r.student_id == student_id), None
        )

    def scores(self, course_id):
        self.store.gr_course(course_id)
        return self.store.gr_scores(course_id)

    def reports_enabled(self, course_id):
        return self.store.gr_course(course_id)[1]

    def report_topics(self, course_id):
        return sorted(
            {r.topic for r in self.store.gr_reports(course_id)}
            | {topic.title for topic in self.service.courses.list_topics(course_id)}
        )

    def save_report(self, card):
        with self.store.transaction():
            _, enabled = self.store.gr_course(card.course_id)
            if not enabled:
                raise AttendanceError("Доклады для этого курса не включены.")
            student = next(
                (s for s in self.students(card.course_id) if s.student_id == card.student_id), None
            )
            if student is None or student.group_id is None:
                raise AttendanceError(
                    "Студент должен входить в состав курса и академическую группу."
                )
            saved = self.report(card.course_id, card.student_id)
            allowed = dict(self.lessons(card.course_id))
            if saved is not None and saved.lesson_id == card.lesson_id:
                allowed.update(self.lessons(card.course_id, include_deleted=True))
            if card.lesson_id not in allowed:
                raise AttendanceError("Выберите неудалённое занятие сдачи этого курса.")
            classmates = {
                s.student_id: s
                for s in self.students(card.course_id)
                if s.group_id == student.group_id
            }
            duplicate = next(
                (
                    r
                    for r in self.store.gr_reports(card.course_id)
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

    def save_scores(self, course_id, changes):
        """Atomic paste: stable (student, work) keys; None clears, zero is a result."""
        with self.store.transaction():
            students = {s.student_id for s in self.students(course_id)}
            works = {w.id: w for w in self.works(course_id)}
            for (student_id, work_id), score in changes.items():
                if student_id not in students or work_id not in works:
                    raise AttendanceError("Ячейка не относится к составу и работам курса.")
                if score is not None and (
                    not isfinite(score) or not 0 <= score <= works[work_id].maximum
                ):
                    raise AttendanceError("Баллы выходят за исходную шкалу работы.")
            for (student_id, work_id), score in changes.items():
                self.store.gr_save_score(student_id, work_id, score)

    def evaluator(self, course_id):
        _, enabled = self.store.gr_course(course_id)
        works = self.works(course_id)
        scores = self.store.gr_scores(course_id)
        reports = {r.student_id: r for r in self.store.gr_reports(course_id)}
        observations = tuple(
            (
                s.student_id,
                {
                    w.id: scores[(s.student_id, w.id)]
                    for w in works
                    if (s.student_id, w.id) in scores
                },
                reports.get(s.student_id),
            )
            for s in self.students(course_id)
        )
        return lambda parameters: tuple(
            calculate(student_id, works, marks, card, enabled, parameters)
            for student_id, marks, card in observations
        )

    def results(self, course_id, parameters=None):
        return self.evaluator(course_id)(parameters or self.parameters(course_id))

    def statistics(self, course_id, parameters=None, *, group_id=None, specialty=None, metric=None):
        ids = {
            s.student_id
            for s in self.students(course_id)
            if (group_id is None or s.group_id == group_id)
            and (specialty is None or s.specialty == specialty)
        }
        results = self.results(course_id, parameters)
        if metric == "report":
            results = tuple(replace(r, final=r.report) for r in results)
        elif isinstance(metric, int):
            work = next((w for w in self.works(course_id) if w.id == metric), None)
            if work is None:
                raise AttendanceError("Работа для статистики не найдена.")
            scores = self.scores(course_id)
            normalized = replace(
                parameters or self.parameters(course_id), final_scale=1.0, final_offset=0.0
            )
            results = tuple(
                calculate(
                    s.student_id,
                    (work,),
                    {work.id: scores[(s.student_id, work.id)]}
                    if (s.student_id, work.id) in scores
                    else {},
                    None,
                    False,
                    normalized,
                )
                for s in self.students(course_id)
            )
        return distribution(tuple(r for r in results if r.student_id in ids))

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
        works = self.works(course_id)
        scores = self.store.gr_scores(course_id)
        cards = {r.student_id: r for r in self.store.gr_reports(course_id)}
        lessons = dict(self.lessons(course_id, include_deleted=True))
        results = {r.student_id: r for r in self.results(course_id)}
        columns = self.store.gr_attendance_columns(course_id)
        attendance_labels = self.store.gr_attendance_labels(course_id)
        topics = dict((number, title) for number, title, _, _ in columns)
        evidence = {
            pair_id: self.service.redesign.pair_evidence(pair_id) for _, _, _, pair_id in columns
        }
        headers = ["ФИО", "№ зачётки", "Группа", "Специальность", "Профилизация", "Подгруппа"]
        headers += [f"Тема {n}: {title} — даты посещения" for n, title in topics.items()]
        headers += [
            "Тема доклада",
            "Занятие сдачи: дата, время, группа",
            "Письменная часть",
            "Оценка доклада /10",
        ]
        headers += [f"{w.title} /{w.maximum:g}" for w in works]
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
                        actual = [e.at for e in grounds if e.kind != "manual"]
                        first = min(actual or [e.at for e in grounds])
                        kind = "Предъявление" if actual else "Подтверждено вручную"
                        events.add(f"{kind}: {first.isoformat()} · {attendance_labels[pair]}")
                row.append("; ".join(sorted(events)))
            row += [
                card.topic if card else "",
                lessons.get(card.lesson_id, "") if card else "",
                ("Да" if card.written else "Нет") if card else "",
                result.report if result is not None else None,
            ]
            row += [scores.get((student.student_id, w.id)) for w in works]
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
