"""Course programme, membership and schedule actions for the Quick worker."""

from datetime import datetime
from zoneinfo import ZoneInfo

from attendance_lab.application.course_commands import (
    CourseCommandContext,
    course_command_availability,
)
from attendance_lab.application.lesson_choices import LessonChoice
from attendance_lab.application.quick_academic_common import (
    calendar_date,
    course_for,
    integer,
    json_safe,
    lesson_for,
    lesson_record,
    optional_id,
    selected_ids,
)
from attendance_lab.domain.models import AttendanceError

MUTATING_COMMANDS = frozenset(
    [
        "academic.add_groups",
        "academic.add_topics",
        "academic.assign_groups",
        "academic.change_topic",
        "academic.copy_course",
        "academic.delete",
        "academic.move",
        "academic.recurrence",
        "academic.restore",
        "academic.save_course",
        "academic.save_discipline",
        "academic.save_roster",
        "academic.schedule",
        "academic.topic",
    ]
)


class CourseActions:
    def __init__(self, service, timezone="Europe/Minsk"):
        self.service, self.timezone = service, timezone

    def snapshot(self, course_id, lesson_id, catalogs):
        api = self.service.courses
        courses = api.list_courses()
        selected = next((course for course in courses if course.id == course_id), None)
        result = {
            "courses": json_safe(courses),
            "course": json_safe(selected),
            "topics": [],
            "rosters": [],
            "lessons": [],
            "course_groups": [],
            "course_students": [],
            "deletions": [
                dict(
                    json_safe(deletion),
                    name=self.service.lifecycle.describe(deletion.lesson_ids[0]),
                )
                for deletion in self.service.lifecycle.list_deletions()
            ],
        }
        if selected is None:
            return result
        result["max_start_slot"] = 9 - selected.duration
        result["course_groups"] = [
            group
            for group in catalogs["groups"]
            if group["academic_year_id"] == selected.academic_year_id
            and group["course"] == selected.study_course
        ]
        result["course_students"] = [
            dict(json_safe(student), id=student.student_id, name=student.full_name)
            for student in self.service.redesign.list_student_contexts(selected.academic_year_id)
            if student.course == selected.study_course
        ]
        topics = api.list_topics(selected.id)
        result["topics"] = [
            dict(json_safe(topic), name=f"{topic.number}. {topic.title}") for topic in topics
        ]
        result["rosters"] = json_safe(api.list_rosters(selected.id))
        for occurrence in api.list_lessons(selected.id):
            block = self.service.redesign.get_block(occurrence.block_id)
            pairs = [self.service.get_lesson(pair_id) for pair_id in block.pair_ids]
            start = min(pair.starts_at for pair in pairs).astimezone(ZoneInfo(block.timezone))
            end = max(pair.ends_at for pair in pairs).astimezone(ZoneInfo(block.timezone))
            topic = next(topic for topic in topics if topic.id == occurrence.topic_id)
            roster = next(roster for roster in result["rosters"] if roster["id"] == block.roster_id)
            state = (
                "open"
                if any(pair.state == "open" for pair in pairs)
                else "completed"
                if all(pair.state == "completed" for pair in pairs)
                else "planned"
            )
            lesson = LessonChoice(
                occurrence.id,
                block.id,
                block.roster_id,
                block.start_slot,
                start,
                end,
                block.timezone,
                topic.title,
                roster["name"],
                state,
            )
            result["lessons"].append(
                {
                    **json_safe(occurrence),
                    **lesson_record(lesson),
                    "name": f"{start:%d.%m.%Y} {lesson.time} · {lesson.group} · {topic.number}. {lesson.topic}",
                    "date": block.date.isoformat(),
                    "is_today": block.date == datetime.now(ZoneInfo(block.timezone)).date(),
                    "pair_ids": list(block.pair_ids),
                    "state": state,
                    "move_reason": self.service.redesign.schedule_edit_reason(block),
                    "can_delete": state != "open",
                    "room": block.room,
                }
            )
        result["lessons"].sort(
            key=lambda lesson: (lesson["date"], lesson["start_slot"], lesson["id"])
        )
        return result

    def availability(self, course_id, lesson_id, has_rows):
        return json_safe(
            course_command_availability(
                self.service,
                CourseCommandContext(course_id, lesson_id=lesson_id, has_journal_rows=has_rows),
            )
        )

    def execute(self, name, payload):
        handlers = {
            "academic.save_discipline": "_save_discipline",
            "academic.save_course": "_save_course",
            "academic.copy_course": "_copy_course",
            "academic.topic": "_topic",
            "academic.add_topics": "_add_topics",
            "academic.add_groups": "_add_groups",
            "academic.assign_groups": "_assign_groups",
            "academic.save_roster": "_save_roster",
            "academic.schedule": "_schedule",
            "academic.move": "_move",
            "academic.recurrence": "_recurrence",
            "academic.change_topic": "_change_topic",
            "academic.delete": "_delete",
            "academic.restore": "_restore",
        }
        if name not in handlers:
            raise AttendanceError("Неизвестная команда раздела.")
        return json_safe(getattr(self, handlers[name])(payload) or {})

    def _save_discipline(self, payload):
        return {
            "discipline_id": self.service.save_discipline(
                str(payload.get("name", "")), optional_id(payload, "discipline_id")
            ).id
        }

    def _save_course(self, payload):
        with self.service.repository.transaction():
            course_id = optional_id(payload, "course_id")
            old = self.service.courses.get_course(course_id) if course_id else None
            discipline_id = optional_id(payload, "discipline_id")
            if discipline_id is None:
                discipline_id = self.service.save_discipline(
                    str(payload.get("discipline_name", ""))
                ).id
            result = self.service.courses.save_course(
                discipline_id,
                integer(payload, "academic_year_id"),
                integer(payload, "semester", 1),
                integer(payload, "study_course", 1),
                str(payload.get("kind", "lecture")),
                integer(payload, "duration", 1),
                str(payload.get("name", "")),
                bool(payload.get("reports_enabled", old.reports_enabled if old else False)),
                course_id,
            )
            return {"course_id": result.id, "select_course_id": result.id}

    def _copy_course(self, payload):
        course = course_for(self.service, payload)
        copied = self.service.courses.copy_course(
            course.id,
            integer(payload, "academic_year_id"),
            integer(payload, "semester"),
            str(payload.get("name", "")),
        )
        return {"course_id": copied.id, "select_course_id": copied.id}

    def _topic(self, payload):
        course = course_for(self.service, payload)
        topic_id = optional_id(payload, "topic_id")
        if topic_id is None:
            topic = self.service.courses.add_topic(
                course.id, integer(payload, "number"), str(payload.get("title", ""))
            )
        else:
            topic = next(
                (
                    item
                    for item in self.service.courses.list_topics(course.id)
                    if item.id == topic_id
                ),
                None,
            )
            if topic is None:
                raise AttendanceError("Тема не относится к выбранному курсу.")
            topic = self.service.courses.save_topic(
                course.id, topic.number, str(payload.get("title", ""))
            )
        return {"topic_id": topic.id}

    def _add_topics(self, payload):
        course = course_for(self.service, payload)
        return {
            "topic_ids": [
                topic.id
                for topic in self.service.courses.add_topics(
                    course.id, integer(payload, "first_number"), integer(payload, "count")
                )
            ]
        }

    def _add_groups(self, payload):
        course = course_for(self.service, payload)
        if course.kind == "lecture":
            raise AttendanceError("Лекция использует весь поток выбранного года и курса обучения.")
        roster = self.service.courses.add_groups(
            course.id,
            selected_ids(payload, "group_ids"),
            payload.get("name") or None,
            selected_ids(payload, "student_ids", numeric=False)
            if payload.get("mode") == "students"
            else None,
        )
        return {"roster_id": roster.id}

    def _assign_groups(self, payload):
        course = course_for(self.service, payload)
        if course.kind == "lecture":
            raise AttendanceError("У лекции нет отдельных подгрупп.")
        roster = self.service.courses.assign_groups(
            course.id, integer(payload, "roster_id"), selected_ids(payload, "group_ids")
        )
        return {"roster_id": roster.id}

    def _save_roster(self, payload):
        course = course_for(self.service, payload)
        roster_id = integer(payload, "roster_id")
        roster = next(
            (item for item in self.service.courses.list_rosters(course.id) if item.id == roster_id),
            None,
        )
        if roster is None or course.kind == "lecture":
            raise AttendanceError("Выберите учебную линию этого курса.")
        group_ids = (
            selected_ids(payload, "group_ids") if "group_ids" in payload else set(roster.group_ids)
        )
        eligible = {
            group.id
            for group in self.service.redesign.list_groups(course.academic_year_id)
            if group.course == course.study_course
        }
        if not group_ids <= eligible:
            raise AttendanceError("Группы должны относиться к учебному году и курсу обучения.")
        result = self.service.redesign.save_roster(
            course.discipline_id,
            course.academic_year_id,
            course.semester,
            str(payload.get("name", roster.name)),
            course.kind,
            course.duration,
            str(payload.get("mode", roster.mode)),
            group_ids,
            selected_ids(payload, "student_ids", numeric=False)
            if "student_ids" in payload
            else set(roster.student_ids),
            roster.id,
        )
        return {"roster_id": result.id}

    def _schedule(self, payload):
        course = course_for(self.service, payload)
        result = self.service.courses.schedule_with_topic(
            course.id,
            optional_id(payload, "roster_id"),
            calendar_date(payload),
            integer(payload, "start_slot"),
            topic_id=optional_id(payload, "topic_id"),
            new_topic_number=optional_id(payload, "new_topic_number"),
            new_topic_title=str(payload.get("new_topic_title", "")),
            interval_days=integer(payload, "interval_days", 0),
            count=integer(payload, "count", 1),
            room=str(payload.get("room", "")),
            timezone=self.timezone,
        )
        lessons = result if isinstance(result, list) else [result]
        return {"lesson_ids": [lesson.id for lesson in lessons], "select_lesson_id": lessons[0].id}

    def _move(self, payload):
        _, lesson = lesson_for(self.service, payload)
        self.service.courses.move_instance(
            lesson.id, calendar_date(payload), integer(payload, "start_slot")
        )
        return {"lesson_id": lesson.id}

    def _recurrence(self, payload):
        _, lesson = lesson_for(self.service, payload)
        if lesson.series_id is None:
            raise AttendanceError("Выбранное занятие не относится к серии.")
        self.service.courses.change_recurrence(
            lesson.series_id, calendar_date(payload), integer(payload, "interval_days")
        )
        return {"series_id": lesson.series_id}

    def _change_topic(self, payload):
        _, lesson = lesson_for(self.service, payload)
        changed = self.service.courses.change_lesson_topic(
            lesson.id,
            optional_id(payload, "topic_id"),
            new_topic_number=optional_id(payload, "new_topic_number"),
            new_topic_title=str(payload.get("new_topic_title", "")),
        )
        return {"lesson_id": changed.id}

    def _delete(self, payload):
        _, lesson = lesson_for(self.service, payload)
        block = self.service.redesign.get_block(lesson.block_id)
        result = self.service.lifecycle.delete_lesson(
            block.pair_ids[0], str(payload.get("reason", ""))
        )
        return {"deletion_id": result.id}

    def _restore(self, payload):
        self.service.lifecycle.restore_deletion(integer(payload, "deletion_id"))
        return {}
