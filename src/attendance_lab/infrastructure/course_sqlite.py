"""SQLite adapter for semester courses; no academic data is guessed on migration."""

import sqlite3
from collections.abc import Callable
from datetime import date

from attendance_lab.domain.course_workflow import (
    CourseLesson,
    CourseSeries,
    CourseTopic,
    SemesterCourse,
)
from attendance_lab.domain.models import AttendanceError

COURSE_SCHEMA = """
CREATE TABLE semester_courses (
 id INTEGER PRIMARY KEY, discipline_id INTEGER NOT NULL REFERENCES disciplines(id),
 academic_year_id INTEGER NOT NULL REFERENCES academic_years(id),
 semester INTEGER NOT NULL CHECK(semester IN (1,2)), study_course INTEGER NOT NULL CHECK(study_course>0),
 kind TEXT NOT NULL CHECK(kind IN ('lecture','lab','seminar','other')),
 duration INTEGER NOT NULL CHECK(duration IN (1,2)), reports_enabled INTEGER NOT NULL DEFAULT 0 CHECK(reports_enabled IN (0,1)), name TEXT NOT NULL
);
CREATE TABLE course_topics (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES semester_courses(id), number INTEGER NOT NULL CHECK(number>0), title TEXT NOT NULL, UNIQUE(course_id,number));
CREATE TABLE course_rosters (course_id INTEGER NOT NULL REFERENCES semester_courses(id), roster_id INTEGER NOT NULL REFERENCES teaching_rosters(id), PRIMARY KEY(course_id,roster_id));
CREATE TABLE course_series (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES semester_courses(id), roster_id INTEGER NOT NULL REFERENCES teaching_rosters(id), first_date TEXT NOT NULL, start_slot INTEGER NOT NULL CHECK(start_slot BETWEEN 1 AND 8), interval_days INTEGER NOT NULL CHECK(interval_days IN (7,14)), topic_number INTEGER NOT NULL, room TEXT NOT NULL, timezone TEXT NOT NULL);
CREATE TABLE course_series_changes (series_id INTEGER NOT NULL REFERENCES course_series(id), effective_date TEXT NOT NULL, interval_days INTEGER NOT NULL CHECK(interval_days IN (7,14)), PRIMARY KEY(series_id,effective_date));
CREATE TABLE course_lessons (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES semester_courses(id), topic_id INTEGER NOT NULL REFERENCES course_topics(id), block_id INTEGER NOT NULL UNIQUE REFERENCES scheduled_blocks(id), series_id INTEGER REFERENCES course_series(id), sequence_index INTEGER);
"""
COURSE_TABLE_COLUMNS = {
    "semester_courses": {
        "id",
        "discipline_id",
        "academic_year_id",
        "semester",
        "study_course",
        "kind",
        "duration",
        "reports_enabled",
        "name",
    },
    "course_topics": {"id", "course_id", "number", "title"},
    "course_rosters": {"course_id", "roster_id"},
    "course_series": {
        "id",
        "course_id",
        "roster_id",
        "first_date",
        "start_slot",
        "interval_days",
        "topic_number",
        "room",
        "timezone",
    },
    "course_series_changes": {"series_id", "effective_date", "interval_days"},
    "course_lessons": {"id", "course_id", "topic_id", "block_id", "series_id", "sequence_index"},
}


class CourseSQLiteMixin:
    _connection: sqlite3.Connection
    _rd_execute: Callable[..., sqlite3.Cursor]

    def cw_courses(self):
        return [
            SemesterCourse(*r)
            for r in self._connection.execute(
                "SELECT * FROM semester_courses ORDER BY academic_year_id DESC,semester DESC,name,id"
            )
        ]

    def cw_save_course(self, c):
        args = (
            c.discipline_id,
            c.academic_year_id,
            c.semester,
            c.study_course,
            c.kind,
            c.duration,
            int(c.reports_enabled),
            c.name,
        )
        if c.id:
            self._rd_execute(
                "UPDATE semester_courses SET discipline_id=?,academic_year_id=?,semester=?,study_course=?,kind=?,duration=?,reports_enabled=?,name=? WHERE id=?",
                args + (c.id,),
            )
            return c
        key = self._rd_execute(
            "INSERT INTO semester_courses(discipline_id,academic_year_id,semester,study_course,kind,duration,reports_enabled,name) VALUES(?,?,?,?,?,?,?,?)",
            args,
        ).lastrowid
        return next(x for x in self.cw_courses() if x.id == key)

    def cw_topics(self, course_id):
        return [
            CourseTopic(*r)
            for r in self._connection.execute(
                "SELECT * FROM course_topics WHERE course_id=? ORDER BY number", (course_id,)
            )
        ]

    def cw_save_topic(self, course_id, number, title):
        self._rd_execute(
            "INSERT INTO course_topics(course_id,number,title) VALUES(?,?,?) ON CONFLICT(course_id,number) DO UPDATE SET title=excluded.title",
            (course_id, number, title),
        )
        return next(t for t in self.cw_topics(course_id) if t.number == number)

    def cw_rosters(self, course_id):
        return {
            r[0]
            for r in self._connection.execute(
                "SELECT roster_id FROM course_rosters WHERE course_id=?", (course_id,)
            )
        }

    def cw_add_roster(self, course_id, roster_id):
        self._rd_execute(
            "INSERT INTO course_rosters VALUES(?,?) ON CONFLICT DO NOTHING", (course_id, roster_id)
        )

    def cw_lessons(self, course_id):
        return [
            CourseLesson(*r)
            for r in self._connection.execute(
                "SELECT * FROM course_lessons WHERE course_id=? ORDER BY id", (course_id,)
            )
        ]

    def cw_add_lesson(self, course_id, topic_id, block_id, series_id=None, sequence_index=None):
        key = self._rd_execute(
            "INSERT INTO course_lessons(course_id,topic_id,block_id,series_id,sequence_index) VALUES(?,?,?,?,?)",
            (course_id, topic_id, block_id, series_id, sequence_index),
        ).lastrowid
        return next(x for x in self.cw_lessons(course_id) if x.id == key)

    def cw_set_lesson_topic(self, course_id, lesson_id, topic_id, title):
        cursor = self._rd_execute(
            "UPDATE course_lessons SET topic_id=? WHERE id=? AND course_id=?",
            (topic_id, lesson_id, course_id),
        )
        if cursor.rowcount != 1:
            raise AttendanceError("Занятие курса не найдено.")
        self._rd_execute(
            "UPDATE scheduled_blocks SET title=? WHERE id=(SELECT block_id FROM course_lessons WHERE id=?)",
            (title, lesson_id),
        )
        self._rd_execute(
            "UPDATE lessons SET title=? WHERE id IN (SELECT bl.lesson_id FROM block_lessons bl JOIN course_lessons cl ON cl.block_id=bl.block_id WHERE cl.id=?)",
            (title, lesson_id),
        )
        return next(lesson for lesson in self.cw_lessons(course_id) if lesson.id == lesson_id)

    def cw_series(self, course_id):
        return [
            CourseSeries(r[0], r[1], r[2], date.fromisoformat(r[3]), *r[4:])
            for r in self._connection.execute(
                "SELECT * FROM course_series WHERE course_id=? ORDER BY id", (course_id,)
            )
        ]

    def cw_save_series(
        self,
        course_id,
        roster_id,
        first_date,
        start_slot,
        interval_days,
        topic_number,
        room,
        timezone,
    ):
        key = self._rd_execute(
            "INSERT INTO course_series(course_id,roster_id,first_date,start_slot,interval_days,topic_number,room,timezone) VALUES(?,?,?,?,?,?,?,?)",
            (
                course_id,
                roster_id,
                first_date.isoformat(),
                start_slot,
                interval_days,
                topic_number,
                room,
                timezone,
            ),
        ).lastrowid
        return next(x for x in self.cw_series(course_id) if x.id == key)

    def cw_sync_topic_title(self, topic_id, title):
        self._rd_execute(
            "UPDATE scheduled_blocks SET title=? WHERE id IN (SELECT block_id FROM course_lessons WHERE topic_id=?)",
            (title, topic_id),
        )
        self._rd_execute(
            "UPDATE lessons SET title=? WHERE id IN (SELECT bl.lesson_id FROM block_lessons bl JOIN course_lessons cl ON cl.block_id=bl.block_id WHERE cl.topic_id=?)",
            (title, topic_id),
        )

    def cw_change_recurrence(self, series_id, from_date, interval_days):
        self._rd_execute(
            "INSERT INTO course_series_changes VALUES(?,?,?) ON CONFLICT(series_id,effective_date) DO UPDATE SET interval_days=excluded.interval_days",
            (series_id, from_date.isoformat(), interval_days),
        )

    def cw_recurrence_changes(self, series_id):
        return [
            (date.fromisoformat(r[0]), r[1])
            for r in self._connection.execute(
                "SELECT effective_date,interval_days FROM course_series_changes WHERE series_id=? ORDER BY effective_date",
                (series_id,),
            )
        ]
