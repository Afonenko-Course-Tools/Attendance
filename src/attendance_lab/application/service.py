"""Attendance use cases; independent from SQL, Qt and the card reader."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4
from zoneinfo import ZoneInfo

from attendance_lab.application.ports import AttendanceRepository, CardRecord, ScanRecord
from attendance_lab.domain.models import (
    AttendanceCell,
    AttendanceError,
    AttendanceReport,
    AttendanceRow,
    CardInfo,
    Discipline,
    Lesson,
    LessonCounts,
    LogRow,
    PublicResult,
    Student,
)
from attendance_lab.domain.redesign import StudentContext
from attendance_lab.domain.rules import (
    normalize_uid,
    required_text,
    utc_datetime,
    validate_timezone,
)

if TYPE_CHECKING:
    from attendance_lab.application.redesign import RedesignService


class AttendanceService:
    def __init__(self, repository: AttendanceRepository) -> None:
        self.repository = repository
        self._redesign_service: RedesignService | None = None

    @property
    def redesign(self) -> RedesignService:
        from attendance_lab.application.redesign import RedesignService

        if self._redesign_service is None:
            self._redesign_service = RedesignService(self)
        return self._redesign_service

    def _discipline(self, discipline_id: int) -> Discipline:
        discipline = self.repository.get_discipline(discipline_id)
        if discipline is None:
            raise AttendanceError("Дисциплина не найдена.")
        return discipline

    def _student(self, student_id: str) -> Student:
        student = self.repository.get_student(student_id)
        if student is None:
            raise AttendanceError("Студент не найден.")
        return student

    def _check_student_ids(self, student_ids: set[str]) -> None:
        known = {student.id for student in self.repository.list_students()}
        if student_ids - known:
            raise AttendanceError("Состав содержит неизвестные ID студентов.")

    def list_disciplines(self) -> list[Discipline]:
        return self.repository.list_disciplines()

    def save_discipline(
        self, name: str, description: str = "", discipline_id: int | None = None
    ) -> Discipline:
        name = required_text(name, "Название дисциплины")
        with self.repository.transaction():
            if discipline_id is not None:
                self._discipline(discipline_id)
            result = self.repository.put_discipline(
                Discipline(discipline_id or 0, name, description.strip())
            )
        return result

    def list_lessons(self, discipline_id: int | None = None) -> list[Lesson]:
        return self.repository.list_lessons(discipline_id)

    def get_lesson(self, lesson_id: int) -> Lesson:
        lesson = self.repository.get_lesson(lesson_id)
        if lesson is None:
            raise AttendanceError("Занятие не найдено.")
        return lesson

    def save_lesson(
        self,
        discipline_id: int,
        starts_at: datetime,
        ends_at: datetime,
        timezone: str,
        title: str = "",
        room: str = "",
        lesson_id: int | None = None,
    ) -> Lesson:
        start, end = utc_datetime(starts_at), utc_datetime(ends_at)
        zone = validate_timezone(timezone)
        if end <= start:
            raise AttendanceError("Окончание занятия должно быть позже начала.")
        with self.repository.transaction():
            self._discipline(discipline_id)
            previous = self.get_lesson(lesson_id) if lesson_id is not None else None
            if (
                previous is not None
                and previous.discipline_id != discipline_id
                and self.repository.list_scans(previous.id)
            ):
                raise AttendanceError("Занятие с отметками нельзя переносить в другую дисциплину.")
            lesson = Lesson(
                lesson_id or 0,
                discipline_id,
                start,
                end,
                zone,
                title.strip(),
                room.strip(),
                previous.state if previous else "planned",
                previous.roster_fixed if previous else False,
            )
            result = self.repository.put_lesson(lesson)
        return result

    def overlapping_lessons(self, lesson: Lesson) -> list[Lesson]:
        return [
            other
            for other in self.repository.list_lessons()
            if other.id != lesson.id
            and other.starts_at < lesson.ends_at
            and lesson.starts_at < other.ends_at
        ]

    def open_lesson(self, lesson_id: int) -> Lesson:
        with self.repository.transaction():
            lesson = self.get_lesson(lesson_id)
            for session in self.redesign.store.rd_sessions():
                if session.closed_at is None:
                    self.redesign.close_session(session.id)
            for other in self.repository.list_lessons():
                if other.state == "open" and other.id != lesson_id:
                    self.repository.put_lesson(replace(other, state="completed"))
            if not lesson.roster_fixed:
                roster = self.repository.get_discipline_roster(lesson.discipline_id)
                self.repository.put_lesson_roster(lesson.id, roster)
            result = self.repository.put_lesson(replace(lesson, state="open", roster_fixed=True))
        return result

    def close_lesson(self, lesson_id: int) -> None:
        with self.repository.transaction():
            lesson = self.get_lesson(lesson_id)
            if not lesson.roster_fixed:
                self.repository.put_lesson_roster(
                    lesson.id,
                    self.repository.get_discipline_roster(lesson.discipline_id),
                )
            self.repository.put_lesson(replace(lesson, state="completed", roster_fixed=True))

    def list_students(self) -> list[Student]:
        return self.repository.list_students()

    def create_student(
        self, full_name: str, group: str = "", student_id: str | None = None
    ) -> Student:
        """Manual creation cannot silently rename the owner of existing attendance."""
        with self.repository.transaction():
            if student_id and self.repository.get_student(student_id.strip()) is not None:
                raise AttendanceError(
                    "Студент с таким ID уже существует. "
                    "Для изменения выберите его в списке и нажмите «Изменить студента»."
                )
            result = self.save_student(full_name, group, student_id)
        return result

    def save_student(
        self, full_name: str, group: str = "", student_id: str | None = None
    ) -> Student:
        student = Student(
            required_text(student_id, "ID студента") if student_id else str(uuid4()),
            required_text(full_name, "ФИО"),
            group.strip(),
        )
        with self.repository.transaction():
            self.repository.put_student(student)
        return student

    def import_students(self, students: list[Student]) -> None:
        normalized = [
            Student(
                required_text(student.id, "ID студента"),
                required_text(student.full_name, "ФИО"),
                student.group.strip(),
            )
            for student in students
        ]
        if len({student.id for student in normalized}) != len(normalized):
            raise AttendanceError("CSV содержит повторяющиеся ID студентов.")
        with self.repository.transaction():
            for student in normalized:
                self.repository.put_student(student)

    def get_discipline_roster(self, discipline_id: int) -> set[str]:
        self._discipline(discipline_id)
        return self.repository.get_discipline_roster(discipline_id)

    def set_discipline_roster(self, discipline_id: int, student_ids: set[str]) -> None:
        with self.repository.transaction():
            self._discipline(discipline_id)
            self._check_student_ids(student_ids)
            self.repository.put_discipline_roster(discipline_id, student_ids)

    def get_lesson_roster(self, lesson_id: int) -> set[str]:
        lesson = self.get_lesson(lesson_id)
        if lesson.roster_fixed:
            return self.repository.get_lesson_roster(lesson.id)
        contexts = self.redesign.get_pair_contexts(lesson_id)
        if any(lesson_id in block.pair_ids for block in self.redesign.list_blocks()):
            return {context.student_id for context in contexts}
        return self.repository.get_discipline_roster(lesson.discipline_id)

    def set_lesson_roster(self, lesson_id: int, student_ids: set[str]) -> None:
        with self.repository.transaction():
            lesson = self.get_lesson(lesson_id)
            self._check_student_ids(student_ids)
            self.repository.put_lesson_roster(lesson.id, student_ids)
            self.repository.put_lesson(replace(lesson, roster_fixed=True))

    def list_cards(self) -> list[CardInfo]:
        with self.repository.transaction(write=False):
            students = {student.id: student for student in self.repository.list_students()}
            scanned: dict[str, set[int]] = {}
            for lesson in self.list_lessons():
                for evidence in self.redesign.pair_evidence(lesson.id):
                    if evidence.uid is not None:
                        scanned.setdefault(evidence.uid, set()).add(lesson.id)
            result = [
                CardInfo(
                    card.uid,
                    card.student_id,
                    students[card.student_id].full_name if card.student_id else None,
                    len(scanned.get(card.uid, set())),
                )
                for card in self.repository.list_cards()
            ]
        return result

    def bind_card(
        self,
        uid: str,
        student_id: str,
        *,
        correction: bool = False,
        expected_student_id: str | None = None,
    ) -> None:
        uid = normalize_uid(uid)
        with self.repository.transaction():
            self._student(student_id)
            card = self.repository.get_card(uid)
            previous_id = card.student_id if card else None
            if (
                correction
                and expected_student_id is not None
                and previous_id != expected_student_id
            ):
                raise AttendanceError("Привязка карты изменилась. Обновите список карт.")
            if previous_id == student_id:
                return
            if previous_id is not None and not correction:
                owner = self._student(previous_id)
                raise AttendanceError(
                    f"Карта уже сопоставлена: {owner.full_name}. "
                    "Используйте явное исправление ошибочной привязки."
                )
            self.repository.put_card(CardRecord(uid, student_id))
            self.repository.add_binding_change(
                uid,
                previous_id,
                student_id,
                datetime.now(UTC),
                correction=previous_id is not None,
            )

    def unbind_card(self, uid: str, *, expected_student_id: str) -> None:
        uid = normalize_uid(uid)
        if not expected_student_id:
            raise AttendanceError("Не указан прежний владелец карты.")
        with self.repository.transaction():
            card = self.repository.get_card(uid)
            if card is None:
                raise AttendanceError("Карта не найдена.")
            if card.student_id is None:
                return
            if card.student_id != expected_student_id:
                raise AttendanceError("Привязка карты изменилась. Обновите список карт.")
            self.repository.put_card(CardRecord(uid, None))
            self.repository.add_binding_change(
                uid, card.student_id, None, datetime.now(UTC), correction=True
            )

    def lesson_log(self, lesson_id: int) -> list[LogRow]:
        with self.repository.transaction(write=False):
            roster = self.get_lesson_roster(lesson_id)
            cards = {card.uid: card for card in self.repository.list_cards()}
            students = {student.id: student for student in self.repository.list_students()}
            by_uid: dict[str, list[datetime]] = {}
            for scan in self.repository.list_scans(lesson_id):
                by_uid.setdefault(scan.uid, []).extend((scan.first_seen, scan.last_seen))
            for evidence in self.redesign.pair_evidence(lesson_id):
                if evidence.uid is not None:
                    by_uid.setdefault(evidence.uid, []).append(evidence.at)
            result = []
            for uid, times in by_uid.items():
                student_id = cards[uid].student_id
                result.append(
                    LogRow(
                        uid,
                        student_id,
                        students[student_id].full_name if student_id else None,
                        min(times),
                        max(times),
                        student_id is not None and student_id not in roster,
                    )
                )
            result.sort(key=lambda row: (row.first_seen, row.uid))
        return result

    def lesson_counts(self, lesson_id: int) -> LessonCounts:
        """Keep expected roster, observed cards and identified people distinct."""
        with self.repository.transaction(write=False):
            rows = self.lesson_log(lesson_id)
            return LessonCounts(
                expected_students=len(self.get_lesson_roster(lesson_id)),
                unique_cards=len(rows),
                identified_students=len(
                    {
                        e.student_id
                        for e in self.redesign.pair_evidence(lesson_id)
                        if e.student_id and e.active
                    }
                ),
                unidentified_cards=sum(row.student_id is None for row in rows),
            )

    def record_scan(self, lesson_id: int, uid: str, at: datetime) -> PublicResult:
        uid, at = normalize_uid(uid), utc_datetime(at)
        with self.repository.transaction():
            lesson = self.get_lesson(lesson_id)
            if lesson.state != "open":
                raise AttendanceError("Регистрация на это занятие закрыта.")
            card = self.repository.get_card(uid)
            if card is None:
                card = CardRecord(uid)
                self.repository.put_card(card)
            previous = self.repository.get_scan(lesson_id, uid)
            repeat = previous is not None
            if card.student_id is not None and not repeat:
                bindings = {
                    candidate.uid: candidate.student_id
                    for candidate in self.repository.list_cards()
                }
                repeat = any(
                    bindings[scan.uid] == card.student_id
                    for scan in self.repository.list_scans(lesson_id)
                )
            self.repository.put_scan(
                ScanRecord(
                    lesson_id,
                    uid,
                    previous.first_seen if previous else at,
                    max(previous.last_seen, at) if previous else at,
                )
            )
            name = self._student(card.student_id).full_name if card.student_id else None
            result = PublicResult(
                "repeat" if repeat else "success",
                "Отметка уже существует" if repeat else "Отметка сохранена",
                name,
                at,
            )
        # Returning outside the transaction prevents a false success on commit failure.
        return result

    def report(
        self,
        discipline_id: int,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        group: str = "",
        student_id: str | None = None,
    ) -> AttendanceReport:
        if date_from and date_to and date_from > date_to:
            raise AttendanceError("Начало периода должно быть не позже окончания.")
        with self.repository.transaction(write=False):
            discipline = self._discipline(discipline_id)
            lessons = tuple(
                lesson
                for lesson in self.repository.list_lessons(discipline_id)
                if (date_from is None or self._lesson_date(lesson) >= date_from)
                and (date_to is None or self._lesson_date(lesson) <= date_to)
            )
            rosters = {lesson.id: self.get_lesson_roster(lesson.id) for lesson in lessons}
            expected = set().union(*rosters.values()) if rosters else set()
            presences: dict[tuple[int, str], datetime] = {}
            manual_only: set[tuple[int, str]] = set()
            unknown: set[str] = set()
            for lesson in lessons:
                evidence = [e for e in self.redesign.pair_evidence(lesson.id) if e.active]
                owners = {e.student_id for e in evidence if e.student_id is not None}
                for ground in evidence:
                    if ground.student_id is None and ground.uid is not None:
                        unknown.add(ground.uid)
                for owner in owners:
                    expected.add(owner)
                    grounds = [e for e in evidence if e.student_id == owner]
                    cards = [e.at for e in grounds if e.kind != "manual"]
                    key = (lesson.id, owner)
                    presences[key] = min(cards or [e.at for e in grounds])
                    if not cards:
                        manual_only.add(key)
            # A period row uses the latest selected frozen context for that
            # student. Unfixed context is a fallback; legacy-only rows retain
            # their original live Student behavior. Per-pair context stays in
            # the snapshots and is not flattened into a global student field.
            frozen_contexts: dict[str, StudentContext] = {}
            future_contexts: dict[str, StudentContext] = {}
            for lesson in sorted(lessons, key=lambda value: (value.starts_at, value.id)):
                target = frozen_contexts if lesson.roster_fixed else future_contexts
                for context in self.redesign.get_pair_contexts(lesson.id):
                    target[context.student_id] = context
            period_contexts = future_contexts | frozen_contexts
            rows = []
            for student in self.repository.list_students():
                context = period_contexts.get(student.id)
                if context is not None:
                    student = replace(
                        student,
                        full_name=context.full_name,
                        group=str(context.group_number)
                        if context.group_number is not None
                        else student.group,
                    )
                if (
                    student.id not in expected
                    or (group and student.group != group)
                    or (student_id is not None and student.id != student_id)
                ):
                    continue
                cells = tuple(
                    self._cell(
                        lesson,
                        student.id in rosters[lesson.id],
                        presences.get((lesson.id, student.id)),
                        (lesson.id, student.id) in manual_only,
                    )
                    for lesson in lessons
                )
                rows.append(AttendanceRow(student, cells))
            result = AttendanceReport(lessons, tuple(rows), len(unknown), discipline.name)
        return result

    @staticmethod
    def _lesson_date(lesson: Lesson) -> date:
        return lesson.starts_at.astimezone(ZoneInfo(lesson.timezone)).date()

    @staticmethod
    def _cell(
        lesson: Lesson, expected: bool, first_seen: datetime | None, manual: bool = False
    ) -> AttendanceCell:
        if first_seen is not None:
            return AttendanceCell("present", first_seen, not expected, manual)
        if not expected:
            return AttendanceCell("outside")
        if lesson.state == "completed":
            return AttendanceCell("missing")
        if lesson.state == "open":
            return AttendanceCell("pending")
        return AttendanceCell("expected")

    def backup(self, path: Path) -> None:
        self.repository.backup(path)

    def recover_interrupted(self) -> int:
        """Explicit startup recovery; registration always requires an operator action."""
        with self.repository.transaction():
            sessions = [
                session
                for session in self.redesign.store.rd_sessions()
                if session.closed_at is None
            ]
            for session in sessions:
                self.redesign.close_session(session.id)
            interrupted = [
                lesson for lesson in self.repository.list_lessons() if lesson.state == "open"
            ]
            for lesson in interrupted:
                self.repository.put_lesson(replace(lesson, state="completed"))
        return len(interrupted) + len(sessions)

    def restore(self, path: Path) -> None:
        if any(lesson.state == "open" for lesson in self.repository.list_lessons()) or any(
            s.closed_at is None for s in self.redesign.store.rd_sessions()
        ):
            raise AttendanceError("Перед восстановлением завершите открытую регистрацию.")
        self.repository.restore(path)
        # A backup can contain an open session; restored data never silently resumes it.
        self.recover_interrupted()

    def close(self) -> None:
        self.repository.close()
