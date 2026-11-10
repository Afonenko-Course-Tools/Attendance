"""Course programme and recurrence use cases, independent of Qt and SQL."""

from dataclasses import replace
from datetime import timedelta

from attendance_lab.domain.course_workflow import SemesterCourse
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.rules import required_text


class CourseWorkflow:
    def __init__(self, service):
        self.service, self.store, self.api = service, service.repository, service.redesign

    def list_courses(self):
        return self.store.cw_courses()

    def get_course(self, course_id):
        value = next((c for c in self.list_courses() if c.id == course_id), None)
        if value is None:
            raise AttendanceError("Курс не найден.")
        return value

    def save_course(
        self,
        discipline_id,
        academic_year_id,
        semester,
        study_course,
        kind,
        duration,
        name,
        reports_enabled=False,
        course_id=None,
    ):
        self.service._discipline(discipline_id)
        if academic_year_id not in {y.id for y in self.api.list_years()}:
            raise AttendanceError("Учебный год не найден.")
        if (
            semester not in (1, 2)
            or (not isinstance(study_course, int) or study_course < 1)
            or duration not in (1, 2)
            or kind not in ("lecture", "lab", "seminar", "other")
        ):
            raise AttendanceError("Неверные параметры курса.")
        c = SemesterCourse(
            course_id or 0,
            discipline_id,
            academic_year_id,
            semester,
            study_course,
            kind,
            duration,
            bool(reports_enabled),
            required_text(name, "Название курса"),
        )
        if course_id:
            old = self.get_course(course_id)
            if (self.list_lessons(course_id) or self.list_rosters(course_id)) and any(
                getattr(old, k) != getattr(c, k)
                for k in (
                    "discipline_id",
                    "academic_year_id",
                    "semester",
                    "study_course",
                    "kind",
                    "duration",
                )
            ):
                raise AttendanceError(
                    "Курс с занятиями нельзя перенести в другой семестр или формат. Создайте копию."
                )
        with self.store.transaction():
            return self.store.cw_save_course(c)

    def list_topics(self, course_id):
        self.get_course(course_id)
        return self.store.cw_topics(course_id)

    def save_topic(self, course_id, number, title):
        self.get_course(course_id)
        if not isinstance(number, int) or number < 1:
            raise AttendanceError("Номер темы должен быть положительным.")
        with self.store.transaction():
            topic = self.store.cw_save_topic(course_id, number, required_text(title, "Тема"))
            self.store.cw_sync_topic_title(topic.id, topic.title)
            return topic

    def add_topic(self, course_id, number, title):
        """Add a programme topic without silently replacing another number."""
        if type(number) is not int or number < 1:
            raise AttendanceError("Номер темы должен быть положительным целым числом.")
        with self.store.transaction():
            if any(topic.number == number for topic in self.list_topics(course_id)):
                raise AttendanceError(
                    "Этот номер уже есть в программе. Выберите другой номер или измените название существующей темы."
                )
            return self.save_topic(course_id, number, title)

    def add_topics(self, course_id, first_number, count):
        """Add programme placeholders only; no lesson or evidence is created."""
        if (
            type(first_number) is not int
            or first_number < 1
            or type(count) is not int
            or not 1 <= count <= 1000
        ):
            raise AttendanceError(
                "Укажите положительный начальный номер и от 1 до 1000 новых тем за один раз."
            )
        with self.store.transaction():
            return [
                self.add_topic(course_id, number, f"Тема {number}")
                for number in range(first_number, first_number + count)
            ]

    def _resolve_topic(self, course_id, topic_id=None, new_topic_number=None, new_topic_title=""):
        if topic_id is None:
            return self.add_topic(course_id, new_topic_number, new_topic_title)
        topic = next((topic for topic in self.list_topics(course_id) if topic.id == topic_id), None)
        if topic is None:
            raise AttendanceError("Выберите тему программы того же курса.")
        return topic

    def change_lesson_topic(
        self, lesson_id, topic_id=None, *, new_topic_number=None, new_topic_title=""
    ):
        """Correct one occurrence's programme topic, preserving all original facts."""
        with self.store.transaction():
            lesson = self.get_lesson(lesson_id)
            topic = self._resolve_topic(
                lesson.course_id, topic_id, new_topic_number, new_topic_title
            )
            return self.store.cw_set_lesson_topic(
                lesson.course_id, lesson.id, topic.id, topic.title
            )

    def schedule_with_topic(
        self,
        course_id,
        roster_id,
        on_date,
        start_slot,
        *,
        topic_id=None,
        new_topic_number=None,
        new_topic_title="",
        interval_days=0,
        count=1,
        room="",
        timezone="Europe/Minsk",
    ):
        """Save the chosen/new topic, optional stream and schedule atomically."""
        with self.store.transaction():
            course = self.get_course(course_id)
            topic = self._resolve_topic(course.id, topic_id, new_topic_number, new_topic_title)
            if course.kind == "lecture" and roster_id is None:
                roster_id = self.lecture_roster(course.id).id
            if interval_days:
                return self.schedule_series(
                    course.id,
                    roster_id,
                    on_date,
                    start_slot,
                    interval_days,
                    topic.number,
                    count,
                    room,
                    timezone,
                )
            return self.schedule(
                course.id, roster_id, topic.number, on_date, start_slot, room, timezone
            )

    def list_rosters(self, course_id):
        ids = self.store.cw_rosters(course_id)
        return [r for r in self.api.list_rosters() if r.id in ids]

    def add_roster(self, course_id, roster_id):
        c, r = self.get_course(course_id), self.api._roster(roster_id)
        if (c.discipline_id, c.academic_year_id, c.semester, c.kind, c.duration) != (
            r.discipline_id,
            r.academic_year_id,
            r.semester,
            r.kind,
            r.duration,
        ):
            raise AttendanceError(
                "Состав должен соответствовать семестру, формату и длительности курса."
            )
        groups = {g.id: g for g in self.api.list_groups()}
        if any(groups[g].course != c.study_course for g in r.group_ids):
            raise AttendanceError("Группы должны относиться к курсу обучения.")
        with self.store.transaction():
            if any(
                c.id != course_id and roster_id in self.store.cw_rosters(c.id)
                for c in self.list_courses()
            ):
                raise AttendanceError(
                    "Состав уже относится к другому курсу. Создайте отдельную подгруппу или копию курса."
                )
            self.store.cw_add_roster(course_id, roster_id)

    def add_groups(self, course_id, group_ids, name=None, student_ids=None):
        c = self.get_course(course_id)
        groups = {g.id: g for g in self.api.list_groups(c.academic_year_id)}
        if (
            not group_ids
            or not group_ids <= groups.keys()
            or any(groups[g].course != c.study_course for g in group_ids)
        ):
            raise AttendanceError("Выберите годовые группы этого курса обучения.")
        with self.store.transaction():
            enrolled = self.api.get_discipline_groups(c.discipline_id, c.academic_year_id)
            self.api.set_discipline_groups(
                c.discipline_id, c.academic_year_id, enrolled | group_ids
            )
            roster = self.api.save_roster(
                c.discipline_id,
                c.academic_year_id,
                c.semester,
                name or "Группы " + ", ".join(str(groups[g].number) for g in sorted(group_ids)),
                c.kind,
                c.duration,
                "students" if student_ids is not None else "groups",
                group_ids,
                student_ids or set(),
            )
            self.add_roster(c.id, roster.id)
            return roster

    def assign_groups(self, course_id, roster_id, group_ids):
        """Explicitly assign annual groups to this teaching line without rewriting history.

        Legacy lines may have been imported as empty student-mode rosters. This
        command intentionally changes their membership mode, preserving their
        identity, scheduled occurrences and all frozen lesson membership/evidence.
        """
        course = self.get_course(course_id)
        roster = next((r for r in self.list_rosters(course_id) if r.id == roster_id), None)
        if roster is None:
            raise AttendanceError("Учебная линия не принадлежит выбранному курсу.")
        groups = {g.id: g for g in self.api.list_groups(course.academic_year_id)}
        group_ids = set(group_ids)
        if (
            not group_ids
            or not group_ids <= groups.keys()
            or any(groups[key].course != course.study_course for key in group_ids)
        ):
            raise AttendanceError("Выберите годовые группы выбранного года и курса обучения.")
        if (
            roster.discipline_id,
            roster.academic_year_id,
            roster.semester,
            roster.kind,
            roster.duration,
        ) != (
            course.discipline_id,
            course.academic_year_id,
            course.semester,
            course.kind,
            course.duration,
        ):
            raise AttendanceError("Параметры учебной линии не соответствуют курсу.")
        with self.store.transaction():
            enrolled = self.api.get_discipline_groups(course.discipline_id, course.academic_year_id)
            self.api.set_discipline_groups(
                course.discipline_id, course.academic_year_id, enrolled | group_ids
            )
            return self.store.rd_save_roster(
                replace(roster, mode="groups", group_ids=tuple(sorted(group_ids)), student_ids=())
            )

    def lecture_roster(self, course_id):
        """Reuse/create the whole selected annual cohort for new lectures, on demand."""
        course = self.get_course(course_id)
        if course.kind != "lecture":
            raise AttendanceError("Общий поток используется только для лекций.")
        groups = {
            g.id
            for g in self.api.list_groups(course.academic_year_id)
            if g.course == course.study_course
        }
        if not groups:
            raise AttendanceError(
                "Сначала загрузите учебные группы выбранного года и курса обучения."
            )
        for roster in self.list_rosters(course_id):
            if roster.mode == "groups" and set(roster.group_ids) == groups:
                return roster
        with self.store.transaction():
            enrolled = self.api.get_discipline_groups(course.discipline_id, course.academic_year_id)
            self.api.set_discipline_groups(
                course.discipline_id, course.academic_year_id, enrolled | groups
            )
            roster = self.api.save_roster(
                course.discipline_id,
                course.academic_year_id,
                course.semester,
                "Общий поток",
                course.kind,
                course.duration,
                "groups",
                groups,
                set(),
            )
            self.add_roster(course_id, roster.id)
            return roster

    def list_students(self, course_id):
        course = self.get_course(course_id)
        if course.kind == "lecture":
            return sorted(
                (
                    replace(student, subgroup="")
                    for student in self.api.list_student_contexts(course.academic_year_id)
                    if student.course == course.study_course
                    and student.academic_year_id == course.academic_year_id
                ),
                key=lambda s: s.full_name,
            )
        students = {}
        for roster in self.list_rosters(course_id):
            students.update((s.student_id, s) for s in self.api._roster_contexts(roster))
        return sorted(students.values(), key=lambda s: s.full_name)

    def list_observed_students(self, course_id):
        """Identified actual attendees, for display without inventing enrollment.

        Includes active card and manual evidence only in this course's live
        occurrences. A late binding or correction changes this projection without
        replaying scans. Missing annual context leaves identity visible with empty
        group fields, rather than guessing membership from another academic year.
        """
        with self.store.transaction(write=False):
            course = self.get_course(course_id)
            observed = {student_id for student_id, _ in self.attendance_projection(course_id)}
            return sorted(
                (
                    student
                    for student in self.api.list_student_contexts(course.academic_year_id)
                    if student.student_id in observed
                ),
                key=lambda student: student.full_name,
            )

    def list_lessons(self, course_id):
        self.get_course(course_id)
        live_blocks = {b.id for b in self.api.list_blocks()}
        return [
            lesson for lesson in self.store.cw_lessons(course_id) if lesson.block_id in live_blocks
        ]

    def get_lesson(self, lesson_id):
        value = next(
            (
                lesson
                for c in self.list_courses()
                for lesson in self.list_lessons(c.id)
                if lesson.id == lesson_id
            ),
            None,
        )
        if value is None:
            raise AttendanceError("Занятие курса не найдено.")
        return value

    def schedule(
        self,
        course_id,
        roster_id,
        topic_number,
        on_date,
        start_slot,
        room="",
        timezone="Europe/Minsk",
        *,
        series_id=None,
        sequence_index=None,
    ):
        if roster_id not in {r.id for r in self.list_rosters(course_id)}:
            raise AttendanceError("Состав не принадлежит курсу.")
        topic = next((t for t in self.list_topics(course_id) if t.number == topic_number), None)
        if topic is None:
            raise AttendanceError("Сначала добавьте тему с этим номером в программу курса.")
        with self.store.transaction():
            b = self.api.schedule_block(
                roster_id, on_date, start_slot, title=topic.title, room=room, timezone=timezone
            )
            return self.store.cw_add_lesson(course_id, topic.id, b.id, series_id, sequence_index)

    def schedule_series(
        self,
        course_id,
        roster_id,
        first_date,
        start_slot,
        interval_days,
        topic_number,
        count,
        room="",
        timezone="Europe/Minsk",
    ):
        if interval_days not in (7, 14) or not 1 <= count <= 100:
            raise AttendanceError("Повторение: 7 или 14 дней, от 1 до 100 занятий.")
        with self.store.transaction():
            series = self.store.cw_save_series(
                course_id,
                roster_id,
                first_date,
                start_slot,
                interval_days,
                topic_number,
                room,
                timezone,
            )
            return [
                self.schedule(
                    course_id,
                    roster_id,
                    topic_number + i,
                    first_date + timedelta(days=i * interval_days),
                    start_slot,
                    room,
                    timezone,
                    series_id=series.id,
                    sequence_index=i,
                )
                for i in range(count)
            ]

    def move_instance(self, lesson_id, on_date, start_slot):
        lesson = self.get_lesson(lesson_id)
        b = self.api.get_block(lesson.block_id)
        with self.store.transaction():
            return self.api.schedule_block(
                b.roster_id,
                on_date,
                start_slot,
                title=b.title,
                room=b.room,
                timezone=b.timezone,
                block_id=b.id,
            )

    def change_recurrence(self, series_id, from_date, interval_days):
        if interval_days not in (7, 14):
            raise AttendanceError("Регулярность: 7 или 14 дней.")
        series = next(
            (
                s
                for c in self.list_courses()
                for s in self.store.cw_series(c.id)
                if s.id == series_id
            ),
            None,
        )
        if series is None:
            raise AttendanceError("Серия не найдена.")
        lessons = sorted(
            (
                lesson
                for lesson in self.list_lessons(series.course_id)
                if lesson.series_id == series_id
                and self.api.get_block(lesson.block_id).date >= from_date
            ),
            key=lambda lesson: lesson.sequence_index,
        )
        with self.store.transaction():
            self.store.cw_change_recurrence(series_id, from_date, interval_days)
            for i, lesson in enumerate(lessons):
                self.move_instance(
                    lesson.id,
                    from_date + timedelta(days=i * interval_days),
                    self.api.get_block(lesson.block_id).start_slot,
                )

    def open_registration(self, lesson_id):
        lesson = self.get_lesson(lesson_id)
        b = self.api.get_block(lesson.block_id)
        return self.api.open_session(b.id, set(b.pair_ids))

    def attendance(self, course_id, student_id, topic_id):
        return any(
            e.student_id == student_id and e.active
            for lesson in self.list_lessons(course_id)
            if lesson.topic_id == topic_id
            for p in self.api.get_block(lesson.block_id).pair_ids
            for e in self.api.pair_evidence(p)
        )

    def copy_course(self, course_id, academic_year_id, semester, name):
        old = self.get_course(course_id)
        with self.store.transaction():
            new = self.save_course(
                old.discipline_id,
                academic_year_id,
                semester,
                old.study_course,
                old.kind,
                old.duration,
                name,
                old.reports_enabled,
            )
            for t in self.list_topics(course_id):
                self.save_topic(new.id, t.number, t.title)
            # Annual groups cannot silently cross academic years.
            if old.academic_year_id == academic_year_id:
                for r in self.list_rosters(old.id):
                    roster = self.api.copy_roster(r.id, semester)
                    self.add_roster(new.id, roster.id)
            return new

    def mark_topics(self, course_id, selected_cells, topic_lessons, reason):
        targets = set()
        for student_id, topic_id in selected_cells:
            lesson = self.get_lesson(topic_lessons.get(topic_id))
            if lesson.course_id != course_id or lesson.topic_id != topic_id:
                raise AttendanceError("Фактическое занятие не соответствует теме и курсу.")
            targets.update(
                (student_id, pair) for pair in self.api.get_block(lesson.block_id).pair_ids
            )
        return self.api.create_manual_batch(targets, reason)

    def attendance_projection(self, course_id, pair_ids=None):
        blocks = {b.id: b for b in self.api.list_blocks()}
        result = set()
        for lesson in self.list_lessons(course_id):
            block = blocks[lesson.block_id]
            if pair_ids is not None and not set(block.pair_ids).intersection(pair_ids):
                continue
            for pair in block.pair_ids:
                result.update(
                    (e.student_id, lesson.topic_id)
                    for e in self.api.pair_evidence(pair)
                    if e.student_id and e.active
                )
        return result
