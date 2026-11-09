"""SQL assessment adapter; caller owns the transaction."""

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from attendance_lab.domain.grading import CalculationParameters, ReportCard, WrittenWork
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import name_key

# A deleted occurrence hides its entire block while preserving every evidence row.
DELETED_BLOCK = """EXISTS(SELECT 1 FROM block_lessons hidden_pair
 JOIN deleted_lessons hidden ON hidden.lesson_id=hidden_pair.lesson_id
 JOIN deletion_batches deletion ON deletion.id=hidden.batch_id
 WHERE hidden_pair.block_id=b.id AND deletion.restored_at IS NULL)"""


def encode_parameters(parameters):
    return json.dumps(asdict(parameters), ensure_ascii=False)


def decode_parameters(payload):
    data = json.loads(payload)
    for key in ("criterion_weights", "three_values", "five_values"):
        data[key] = tuple(data[key])
    data["work_weights"] = tuple(tuple(v) for v in data["work_weights"])
    return CalculationParameters(**data)


class GradingSQLiteMixin:
    _connection: sqlite3.Connection

    def gr_works(self, course_id):
        return tuple(
            WrittenWork(*r)
            for r in self._connection.execute(
                "SELECT id,course_id,title,maximum,weight FROM course_works WHERE course_id=? ORDER BY id",
                (course_id,),
            )
        )

    def gr_add_work(self, course_id, title, maximum, weight):
        cursor = self._connection.execute(
            "INSERT INTO course_works(course_id,title,maximum,weight) VALUES(?,?,?,?)",
            (course_id, title, maximum, weight),
        )
        assert cursor.lastrowid is not None
        return WrittenWork(cursor.lastrowid, course_id, title, maximum, weight)

    def gr_scores(self, course_id):
        return {
            (r[0], r[1]): r[2]
            for r in self._connection.execute(
                "SELECT s.student_id,s.work_id,s.score FROM course_scores s JOIN course_works w "
                "ON w.id=s.work_id WHERE w.course_id=?",
                (course_id,),
            )
        }

    def gr_save_score(self, student_id, work_id, score):
        if score is None:
            self._connection.execute(
                "DELETE FROM course_scores WHERE work_id=? AND student_id=?", (work_id, student_id)
            )
        else:
            self._connection.execute(
                "INSERT INTO course_scores VALUES(?,?,?) "
                "ON CONFLICT(work_id,student_id) DO UPDATE SET score=excluded.score",
                (work_id, student_id, score),
            )

    def gr_reports(self, course_id):
        return tuple(
            ReportCard(r[0], r[1], r[2], r[3], bool(r[4]), tuple(json.loads(r[5])))
            for r in self._connection.execute(
                "SELECT course_id,student_id,lesson_id,topic,written,levels "
                "FROM course_reports WHERE course_id=?",
                (course_id,),
            )
        )

    def gr_save_report(self, card, group_id):
        try:
            self._connection.execute(
                "INSERT INTO course_reports VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(course_id,student_id) DO UPDATE SET lesson_id=excluded.lesson_id, "
                "group_id=excluded.group_id,topic=excluded.topic,topic_key=excluded.topic_key, "
                "written=excluded.written,levels=excluded.levels",
                (
                    card.course_id,
                    card.student_id,
                    card.lesson_id,
                    group_id,
                    card.topic.strip(),
                    name_key(card.topic),
                    int(card.written),
                    json.dumps(card.levels),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AttendanceError(
                "Тема уже занята в этой группе или занятие не относится к курсу."
            ) from exc

    def gr_parameters(self, course_id):
        row = self._connection.execute(
            "SELECT parameters FROM course_calculations WHERE course_id=? "
            "ORDER BY version DESC LIMIT 1",
            (course_id,),
        ).fetchone()
        return decode_parameters(row[0]) if row else CalculationParameters()

    def gr_apply_parameters(self, course_id, parameters):
        self._connection.execute(
            "INSERT INTO course_calculations VALUES(?,?,?,?)",
            (
                course_id,
                parameters.version,
                encode_parameters(parameters),
                datetime.now(UTC).isoformat(),
            ),
        )

    def gr_save_template(self, name, parameters, works):
        payload = json.dumps(
            {
                "parameters": asdict(parameters),
                "works": {str(w.id): [name_key(w.title), w.maximum] for w in works},
            },
            ensure_ascii=False,
        )
        self._connection.execute(
            "INSERT INTO calculation_templates VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET parameters=excluded.parameters",
            (name, payload),
        )

    def gr_templates(self, works=None):
        from dataclasses import replace

        templates = {}
        for name, raw in self._connection.execute(
            "SELECT name,parameters FROM calculation_templates ORDER BY name"
        ):
            payload = json.loads(raw)
            parameters = decode_parameters(json.dumps(payload["parameters"]))
            if works is not None:
                mapped = []
                for old_id, weight in parameters.work_weights:
                    identity = payload["works"].get(str(old_id))
                    matches = [w for w in works if [name_key(w.title), w.maximum] == identity]
                    if len(matches) != 1:
                        break
                    mapped.append((matches[0].id, weight))
                else:
                    templates[name] = replace(parameters, work_weights=tuple(mapped))
                continue
            templates[name] = parameters
        return templates

    def gr_course(self, course_id):
        row = self._connection.execute(
            "SELECT academic_year_id,reports_enabled FROM semester_courses WHERE id=?", (course_id,)
        ).fetchone()
        if row is None:
            raise AttendanceError("Курс не найден.")
        return row[0], bool(row[1])

    def gr_lessons(self, course_id, include_deleted=False):
        rows = self._connection.execute(
            "SELECT l.id,MIN(p.starts_at),b.timezone,t.title,r.name,"
            + DELETED_BLOCK
            + " FROM course_lessons l "
            "JOIN scheduled_blocks b ON b.id=l.block_id "
            "JOIN block_lessons bl ON bl.block_id=b.id JOIN lessons p ON p.id=bl.lesson_id "
            "JOIN teaching_rosters r ON r.id=b.roster_id "
            "JOIN course_topics t ON t.id=l.topic_id WHERE l.course_id=? "
            "GROUP BY l.id ORDER BY b.day,b.start_slot,l.id",
            (course_id,),
        )
        return tuple(
            (
                row[0],
                f"{datetime.fromisoformat(row[1]).astimezone(ZoneInfo(row[2])):%d.%m.%Y %H:%M} · {row[3]} · {row[4]}"
                + (" (удалено)" if row[5] else ""),
            )
            for row in rows
            if include_deleted or not row[5]
        )

    def gr_next_version(self, course_id):
        return (
            self._connection.execute(
                "SELECT MAX(version) FROM course_calculations WHERE course_id=?", (course_id,)
            ).fetchone()[0]
            or 1
        ) + 1

    def gr_attendance_columns(self, course_id):
        return tuple(
            (r[0], r[1], r[2], r[3])
            for r in self._connection.execute(
                "SELECT t.number,t.title,b.day,bl.lesson_id FROM course_lessons cl "
                "JOIN course_topics t ON t.id=cl.topic_id JOIN scheduled_blocks b ON b.id=cl.block_id "
                "JOIN block_lessons bl ON bl.block_id=b.id WHERE cl.course_id=? AND NOT "
                + DELETED_BLOCK
                + " ORDER BY t.number,b.day,bl.slot",
                (course_id,),
            )
        )

    def gr_attendance_labels(self, course_id):
        return {
            r[0]: f"{r[1]} · пара {r[2]} · {r[3]}"
            for r in self._connection.execute(
                "SELECT bl.lesson_id,b.day,b.start_slot,r.name FROM course_lessons cl "
                "JOIN scheduled_blocks b ON b.id=cl.block_id JOIN block_lessons bl ON bl.block_id=b.id "
                "JOIN teaching_rosters r ON r.id=b.roster_id WHERE cl.course_id=? AND NOT "
                + DELETED_BLOCK,
                (course_id,),
            )
        }
