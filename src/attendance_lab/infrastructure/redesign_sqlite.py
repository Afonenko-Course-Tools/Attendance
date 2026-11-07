"""Relational catalog and evidence persistence, added without rewriting V1 history."""

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime

from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import (
    AcademicGroup,
    AcademicYear,
    Evidence,
    ManualBatch,
    Profile,
    RegistrationSession,
    ScheduledBlock,
    Specialty,
    StudentContext,
    TeachingRoster,
    name_key,
)
from attendance_lab.infrastructure.redesign_schema import SCHEMA_V2 as SCHEMA_V2
from attendance_lab.infrastructure.redesign_schema import V2_TABLE_COLUMNS as V2_TABLE_COLUMNS


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AttendanceError("Необходимо время с часовым поясом.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class RedesignSQLiteMixin:
    """Focused SQL adapter mixed into the legacy transactional repository."""

    _connection: sqlite3.Connection

    def _rd_execute(self, sql, values=()):
        try:
            return self._connection.execute(sql, values)
        except sqlite3.IntegrityError as exc:
            raise AttendanceError("Изменение нарушает связи или уникальность справочника.") from exc

    def rd_revision(self) -> str:
        tables = (
            "students",
            "academic_years",
            "courses",
            "specialties",
            "specialty_aliases",
            "profiles",
            "profile_aliases",
            "academic_groups",
            "annual_memberships",
        )
        data = [
            [tuple(row) for row in self._connection.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
            for table in tables
        ]
        return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()

    def rd_years(self):
        return [
            AcademicYear(*r)
            for r in self._connection.execute(
                "SELECT id,start_year FROM academic_years ORDER BY start_year"
            )
        ]

    def rd_save_year(self, start_year):
        self._rd_execute(
            "INSERT INTO academic_years(start_year) VALUES(?) ON CONFLICT DO NOTHING", (start_year,)
        )
        return next(y for y in self.rd_years() if y.start_year == start_year)

    def rd_specialties(self):
        return [
            Specialty(*r)
            for r in self._connection.execute("SELECT id,name FROM specialties ORDER BY name,id")
        ]

    def rd_profiles(self):
        return [
            Profile(*r)
            for r in self._connection.execute(
                "SELECT id,specialty_id,name FROM profiles ORDER BY name,id"
            )
        ]

    def rd_resolve_specialty(self, key):
        row = self._connection.execute(
            "SELECT specialty_id FROM specialty_aliases WHERE normalized_key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def rd_resolve_profile(self, specialty_id, key):
        row = self._connection.execute(
            "SELECT profile_id FROM profile_aliases WHERE specialty_id=? AND normalized_key=?",
            (specialty_id, key),
        ).fetchone()
        return row[0] if row else None

    def rd_specialty_alias(self, specialty_id, name):
        key = name_key(name)
        current = self.rd_resolve_specialty(key)
        if current is not None and current != specialty_id:
            raise AttendanceError("Название или псевдоним уже относится к другой специальности.")
        self._rd_execute(
            "INSERT INTO specialty_aliases VALUES(?,?) ON CONFLICT DO NOTHING", (key, specialty_id)
        )

    def rd_profile_alias(self, profile_id, name):
        profile = next((p for p in self.rd_profiles() if p.id == profile_id), None)
        if profile is None:
            raise AttendanceError("Профилизация не найдена.")
        key = name_key(name)
        current = self.rd_resolve_profile(profile.specialty_id, key)
        if current is not None and current != profile_id:
            raise AttendanceError("Название или псевдоним уже относится к другой профилизации.")
        self._rd_execute(
            "INSERT INTO profile_aliases VALUES(?,?,?) ON CONFLICT DO NOTHING",
            (profile.specialty_id, key, profile_id),
        )

    def rd_save_specialty(self, name, entity_id=0):
        if entity_id:
            self.rd_specialty_alias(entity_id, name)
            self._rd_execute(
                "UPDATE specialties SET name=?,normalized_key=? WHERE id=?",
                (name, name_key(name), entity_id),
            )
        else:
            entity_id = self._rd_execute(
                "INSERT INTO specialties(name,normalized_key) VALUES(?,?)", (name, name_key(name))
            ).lastrowid
            self.rd_specialty_alias(entity_id, name)
        assert entity_id is not None
        return Specialty(entity_id, name)

    def rd_save_profile(self, specialty_id, name, entity_id=0):
        if entity_id:
            self.rd_profile_alias(entity_id, name)
            self._rd_execute(
                "UPDATE profiles SET name=?,normalized_key=? WHERE id=?",
                (name, name_key(name), entity_id),
            )
        else:
            entity_id = self._rd_execute(
                "INSERT INTO profiles(specialty_id,name,normalized_key) VALUES(?,?,?)",
                (specialty_id, name, name_key(name)),
            ).lastrowid
            self.rd_profile_alias(entity_id, name)
        assert entity_id is not None
        return Profile(entity_id, specialty_id, name)

    def rd_groups(self):
        return [
            AcademicGroup(*r)
            for r in self._connection.execute(
                "SELECT g.id,g.academic_year_id,g.specialty_id,c.number,g.group_number,g.variant_key,s.name "
                "FROM academic_groups g JOIN courses c ON c.id=g.course_id JOIN specialties s ON s.id=g.specialty_id ORDER BY c.number,g.group_number,g.id"
            )
        ]

    def rd_save_group(self, value):
        self._rd_execute(
            "INSERT INTO courses(number) VALUES(?) ON CONFLICT DO NOTHING", (value.course,)
        )
        course_id = self._connection.execute(
            "SELECT id FROM courses WHERE number=?", (value.course,)
        ).fetchone()[0]
        values = (
            value.academic_year_id,
            value.specialty_id,
            course_id,
            value.number,
            value.variant_key,
        )
        if value.id:
            self._rd_execute(
                "UPDATE academic_groups SET academic_year_id=?,specialty_id=?,course_id=?,group_number=?,variant_key=? WHERE id=?",
                (*values, value.id),
            )
            entity_id = value.id
        else:
            entity_id = self._rd_execute(
                "INSERT INTO academic_groups(academic_year_id,specialty_id,course_id,group_number,variant_key) VALUES(?,?,?,?,?)",
                values,
            ).lastrowid
        return next(g for g in self.rd_groups() if g.id == entity_id)

    def rd_contexts(self, year_id=None):
        # No implicit new membership: unresolved legacy students have empty context.
        query = (
            "SELECT s.id,s.full_name,coalesce(s.record_book_number,''),m.academic_year_id,g.id,g.group_number,c.number,"
            "coalesce(sp.name,''),coalesce(p.name,'') FROM students s "
            "LEFT JOIN annual_memberships m ON m.student_id=s.id AND m.academic_year_id="
            + (
                "?"
                if year_id is not None
                else "(SELECT y.id FROM academic_years y JOIN annual_memberships a ON a.academic_year_id=y.id WHERE a.student_id=s.id ORDER BY y.start_year DESC LIMIT 1)"
            )
            + " LEFT JOIN academic_groups g ON g.id=m.group_id LEFT JOIN courses c ON c.id=g.course_id "
            "LEFT JOIN specialties sp ON sp.id=g.specialty_id LEFT JOIN profiles p ON p.id=m.profile_id ORDER BY s.full_name,s.id"
        )
        return [
            StudentContext(*r)
            for r in self._connection.execute(query, (year_id,) if year_id is not None else ())
        ]

    def rd_save_book(self, student_id, number):
        self._rd_execute(
            "UPDATE students SET record_book_number=? WHERE id=?", (number or None, student_id)
        )

    def rd_membership(self, student_id, year_id, group_id, profile_id):
        group = next(g for g in self.rd_groups() if g.id == group_id)
        old = self._connection.execute(
            "SELECT group_id,profile_id FROM annual_memberships WHERE student_id=? AND academic_year_id=?",
            (student_id, year_id),
        ).fetchone()
        if old is not None and tuple(old) == (group_id, profile_id):
            return
        self._rd_execute(
            "INSERT INTO membership_changes(student_id,academic_year_id,previous_group_id,group_id,previous_profile_id,profile_id,changed_at) VALUES(?,?,?,?,?,?,?)",
            (
                student_id,
                year_id,
                old[0] if old else None,
                group_id,
                old[1] if old else None,
                profile_id,
                stamp(datetime.now(UTC)),
            ),
        )
        self._rd_execute(
            "INSERT INTO annual_memberships(student_id,academic_year_id,group_id,specialty_id,profile_id) VALUES(?,?,?,?,?) "
            "ON CONFLICT(student_id,academic_year_id) DO UPDATE SET group_id=excluded.group_id,specialty_id=excluded.specialty_id,profile_id=excluded.profile_id",
            (student_id, year_id, group_id, group.specialty_id, profile_id),
        )

    def rd_import_batch(self, year_id, plan, student_ids):
        batch_id = self._rd_execute(
            "INSERT INTO import_batches(academic_year_id,imported_at,file_hash,state) VALUES(?,?,?,'applied')",
            (year_id, stamp(datetime.now(UTC)), plan.file_hash),
        ).lastrowid
        for row, student_id in zip(plan.rows, student_ids, strict=True):
            self._rd_execute(
                "INSERT INTO import_rows VALUES(?,?,?,?)",
                (batch_id, student_id, row.source_row, row.ordinal),
            )

    def rd_discipline_groups(self, discipline_id, year_id):
        return {
            r[0]
            for r in self._connection.execute(
                "SELECT d.group_id FROM discipline_groups d JOIN academic_groups g ON g.id=d.group_id WHERE d.discipline_id=? AND g.academic_year_id=?",
                (discipline_id, year_id),
            )
        }

    def rd_set_discipline_groups(self, discipline_id, year_id, groups):
        self._rd_execute(
            "DELETE FROM discipline_groups WHERE discipline_id=? AND group_id IN (SELECT id FROM academic_groups WHERE academic_year_id=?)",
            (discipline_id, year_id),
        )
        for group_id in sorted(groups):
            self._rd_execute("INSERT INTO discipline_groups VALUES(?,?)", (discipline_id, group_id))

    def rd_rosters(self):
        result = []
        for r in self._connection.execute(
            "SELECT id,discipline_id,academic_year_id,semester,name,kind,duration,mode FROM teaching_rosters ORDER BY id"
        ):
            groups = tuple(
                x[0]
                for x in self._connection.execute(
                    "SELECT group_id FROM roster_groups WHERE roster_id=? ORDER BY group_id",
                    (r[0],),
                )
            )
            students = tuple(
                x[0]
                for x in self._connection.execute(
                    "SELECT student_id FROM roster_students WHERE roster_id=? ORDER BY student_id",
                    (r[0],),
                )
            )
            result.append(
                TeachingRoster(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], groups, students)
            )
        return result

    def rd_save_roster(self, value):
        values = (
            value.discipline_id,
            value.academic_year_id,
            value.semester,
            value.name,
            value.kind,
            value.duration,
            value.mode,
        )
        if value.id:
            entity_id = value.id
            self._rd_execute(
                "UPDATE teaching_rosters SET discipline_id=?,academic_year_id=?,semester=?,name=?,kind=?,duration=?,mode=? WHERE id=?",
                (*values, entity_id),
            )
            self._rd_execute("DELETE FROM roster_groups WHERE roster_id=?", (entity_id,))
            self._rd_execute("DELETE FROM roster_students WHERE roster_id=?", (entity_id,))
        else:
            entity_id = self._rd_execute(
                "INSERT INTO teaching_rosters(discipline_id,academic_year_id,semester,name,kind,duration,mode) VALUES(?,?,?,?,?,?,?)",
                values,
            ).lastrowid
        for group_id in value.group_ids:
            self._rd_execute("INSERT INTO roster_groups VALUES(?,?)", (entity_id, group_id))
        for student_id in value.student_ids:
            self._rd_execute("INSERT INTO roster_students VALUES(?,?)", (entity_id, student_id))
        return replace(value, id=entity_id)

    def rd_blocks(self):
        return [
            ScheduledBlock(
                r["id"],
                r["roster_id"],
                date.fromisoformat(r["day"]),
                r["start_slot"],
                tuple(
                    x[0]
                    for x in self._connection.execute(
                        "SELECT lesson_id FROM block_lessons WHERE block_id=? ORDER BY slot",
                        (r["id"],),
                    )
                ),
                r["title"],
                r["room"],
                r["timezone"],
            )
            for r in self._connection.execute(
                "SELECT * FROM scheduled_blocks ORDER BY day,start_slot,id"
            )
        ]

    def rd_save_block(self, value):
        values = (
            value.roster_id,
            value.date.isoformat(),
            value.start_slot,
            value.timezone,
            value.title,
            value.room,
        )
        if value.id:
            entity_id = value.id
            self._rd_execute(
                "UPDATE scheduled_blocks SET roster_id=?,day=?,start_slot=?,timezone=?,title=?,room=? WHERE id=?",
                (*values, entity_id),
            )
            self._rd_execute("DELETE FROM block_lessons WHERE block_id=?", (entity_id,))
        else:
            entity_id = self._rd_execute(
                "INSERT INTO scheduled_blocks(roster_id,day,start_slot,timezone,title,room) VALUES(?,?,?,?,?,?)",
                values,
            ).lastrowid
        for offset, pair_id in enumerate(value.pair_ids):
            self._rd_execute(
                "INSERT INTO block_lessons VALUES(?,?,?)",
                (entity_id, pair_id, value.start_slot + offset),
            )
        return replace(value, id=entity_id)

    def rd_context_snapshot(self, pair_id):
        return [
            StudentContext(
                r["student_id"],
                r["full_name"],
                r["record_book_number"] or "",
                r["academic_year_id"],
                r["group_id"],
                r["group_number"],
                r["course"],
                r["specialty_name"],
                r["profile_name"],
                r["subgroup"],
            )
            for r in self._connection.execute(
                "SELECT * FROM lesson_context WHERE lesson_id=? ORDER BY full_name,student_id",
                (pair_id,),
            )
        ]

    def rd_save_snapshot(self, pair_id, contexts, reason=""):
        if reason:
            before = json.dumps(
                [
                    dict(r)
                    for r in self._connection.execute(
                        "SELECT * FROM lesson_context WHERE lesson_id=?", (pair_id,)
                    )
                ],
                ensure_ascii=False,
            )
            self._rd_execute(
                "INSERT INTO roster_adjustments(lesson_id,reason,changed_at,previous_context,new_context) VALUES(?,?,?,?,?)",
                (
                    pair_id,
                    reason,
                    stamp(datetime.now(UTC)),
                    before,
                    json.dumps([str(c) for c in contexts], ensure_ascii=False),
                ),
            )
        self._rd_execute("DELETE FROM lesson_context WHERE lesson_id=?", (pair_id,))
        for value in contexts:
            profile = next(
                (
                    p
                    for p in self.rd_profiles()
                    if p.name == value.profile
                    and p.specialty_id
                    == next(g.specialty_id for g in self.rd_groups() if g.id == value.group_id)
                ),
                None,
            )
            self._rd_execute(
                "INSERT INTO lesson_context VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pair_id,
                    value.student_id,
                    value.group_id,
                    profile.id if profile else None,
                    value.full_name,
                    value.record_book_number,
                    str(value.group_number),
                    value.specialty,
                    value.academic_year_id,
                    value.group_number,
                    value.course,
                    value.profile,
                    value.subgroup,
                ),
            )

    def rd_sessions(self):
        return [
            RegistrationSession(
                r["id"],
                r["block_id"],
                tuple(
                    x[0]
                    for x in self._connection.execute(
                        "SELECT pair_id FROM session_target_pairs WHERE session_id=? ORDER BY pair_id",
                        (r["id"],),
                    )
                ),
                datetime.fromisoformat(r["opened_at"]),
                datetime.fromisoformat(r["closed_at"]) if r["closed_at"] else None,
            )
            for r in self._connection.execute("SELECT * FROM registration_sessions ORDER BY id")
        ]

    def rd_save_session(self, value, *, audit=False):
        if value.id:
            entity_id = value.id
            if audit:
                old = next(s for s in self.rd_sessions() if s.id == entity_id)
                self._rd_execute(
                    "INSERT INTO session_scope_changes(session_id,changed_at,previous_targets,target_pairs) VALUES(?,?,?,?)",
                    (
                        entity_id,
                        stamp(datetime.now(UTC)),
                        json.dumps(old.target_pair_ids),
                        json.dumps(value.target_pair_ids),
                    ),
                )
            self._rd_execute(
                "UPDATE registration_sessions SET closed_at=?,state=? WHERE id=?",
                (
                    stamp(value.closed_at) if value.closed_at else None,
                    "closed" if value.closed_at else "open",
                    entity_id,
                ),
            )
            self._rd_execute("DELETE FROM session_target_pairs WHERE session_id=?", (entity_id,))
        else:
            entity_id = self._rd_execute(
                "INSERT INTO registration_sessions(block_id,opened_at,closed_at,state) VALUES(?,?,?,?)",
                (
                    value.block_id,
                    stamp(value.opened_at),
                    stamp(value.closed_at) if value.closed_at else None,
                    "closed" if value.closed_at else "open",
                ),
            ).lastrowid
        for pair_id in value.target_pair_ids:
            self._rd_execute("INSERT INTO session_target_pairs VALUES(?,?)", (entity_id, pair_id))
        return replace(value, id=entity_id)

    def rd_add_card_evidence(self, session_id, uid, at):
        self._rd_execute(
            "INSERT INTO card_evidence(session_id,uid,observed_at) VALUES(?,?,?)",
            (session_id, uid, stamp(at)),
        )

    def rd_pair_evidence(self, pair_id):
        result = [
            Evidence(
                "legacy_card",
                pair_id,
                r["student_id"],
                datetime.fromisoformat(r["first_seen"]),
                r["uid"],
            )
            for r in self._connection.execute(
                "SELECT s.*,c.student_id FROM scans s JOIN cards c ON c.uid=s.uid WHERE s.lesson_id=?",
                (pair_id,),
            )
        ]
        result += [
            Evidence(
                "card",
                pair_id,
                r["student_id"],
                datetime.fromisoformat(r["observed_at"]),
                r["uid"],
                r["session_id"],
            )
            for r in self._connection.execute(
                "SELECT e.*,c.student_id FROM card_evidence e JOIN cards c ON c.uid=e.uid JOIN session_target_pairs t ON t.session_id=e.session_id WHERE t.pair_id=?",
                (pair_id,),
            )
        ]
        result += [
            Evidence(
                "manual",
                pair_id,
                r["student_id"],
                datetime.fromisoformat(r["confirmed_at"]),
                None,
                r["session_id"],
                r["batch_id"],
                r["revoked_at"] is None,
            )
            for r in self._connection.execute(
                "SELECT e.*,b.confirmed_at,b.revoked_at FROM manual_evidence e JOIN manual_batches b ON b.id=e.batch_id "
                "WHERE e.pair_id=? OR (e.session_id IS NOT NULL AND EXISTS(SELECT 1 FROM session_target_pairs t WHERE t.session_id=e.session_id AND t.pair_id=?))",
                (pair_id, pair_id),
            )
        ]
        return sorted(result, key=lambda e: e.at)

    def rd_batches(self):
        result = []
        for r in self._connection.execute("SELECT * FROM manual_batches ORDER BY id"):
            items = self._connection.execute(
                "SELECT * FROM manual_evidence WHERE batch_id=? ORDER BY student_id,pair_id",
                (r["id"],),
            ).fetchall()
            session_id = items[0]["session_id"] if items else None
            if session_id:
                targets = tuple(
                    (item["student_id"], t[0])
                    for item in items
                    for t in self._connection.execute(
                        "SELECT pair_id FROM session_target_pairs WHERE session_id=? ORDER BY pair_id",
                        (session_id,),
                    )
                )
            else:
                targets = tuple((item["student_id"], item["pair_id"]) for item in items)
            result.append(
                ManualBatch(
                    r["id"],
                    targets,
                    r["reason"],
                    datetime.fromisoformat(r["confirmed_at"]),
                    datetime.fromisoformat(r["revoked_at"]) if r["revoked_at"] else None,
                    session_id,
                )
            )
        return result

    def rd_save_batch(self, value, student_ids=()):
        if value.id:
            self._rd_execute(
                "UPDATE manual_batches SET revoked_at=? WHERE id=?",
                (stamp(value.revoked_at) if value.revoked_at else None, value.id),
            )
            return value
        entity_id = self._rd_execute(
            "INSERT INTO manual_batches(reason,confirmed_at,revoked_at) VALUES(?,?,NULL)",
            (value.reason, stamp(value.created_at)),
        ).lastrowid
        if value.session_id:
            for student_id in student_ids:
                self._rd_execute(
                    "INSERT INTO manual_evidence(batch_id,student_id,session_id) VALUES(?,?,?)",
                    (entity_id, student_id, value.session_id),
                )
        else:
            for student_id, pair_id in value.targets:
                self._rd_execute(
                    "INSERT INTO manual_evidence(batch_id,student_id,pair_id) VALUES(?,?,?)",
                    (entity_id, student_id, pair_id),
                )
        return replace(value, id=entity_id)
