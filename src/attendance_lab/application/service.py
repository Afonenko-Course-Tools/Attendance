"""Attendance use cases; independent from SQL, Qt and the card reader."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
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
from attendance_lab.domain.rules import (
    normalize_uid,
    required_text,
    utc_datetime,
    validate_timezone,
)


class AttendanceService:
    def __init__(self, repository: AttendanceRepository) -> None:
        self.repository = repository

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
            for scan in self.repository.list_scans():
                scanned.setdefault(scan.uid, set()).add(scan.lesson_id)
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

    def bind_card(self, uid: str, student_id: str, *, correction: bool = False) -> None:
        uid = normalize_uid(uid)
        with self.repository.transaction():
            self._student(student_id)
            card = self.repository.get_card(uid)
            previous_id = card.student_id if card else None
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

    def lesson_log(self, lesson_id: int) -> list[LogRow]:
        with self.repository.transaction(write=False):
            roster = self.get_lesson_roster(lesson_id)
            cards = {card.uid: card for card in self.repository.list_cards()}
            students = {student.id: student for student in self.repository.list_students()}
            result = []
            for scan in self.repository.list_scans(lesson_id):
                student_id = cards[scan.uid].student_id
                result.append(
                    LogRow(
                        scan.uid,
                        student_id,
                        students[student_id].full_name if student_id else None,
                        scan.first_seen,
                        scan.last_seen,
                        student_id is not None and student_id not in roster,
                    )
                )
        return result

    def lesson_counts(self, lesson_id: int) -> LessonCounts:
        """Keep expected roster, observed cards and identified people distinct."""
        with self.repository.transaction(write=False):
            rows = self.lesson_log(lesson_id)
            return LessonCounts(
                expected_students=len(self.get_lesson_roster(lesson_id)),
                unique_cards=len(rows),
                identified_students=len({row.student_id for row in rows if row.student_id}),
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
            bindings = {card.uid: card.student_id for card in self.repository.list_cards()}
            presences: dict[tuple[int, str], datetime] = {}
            unknown: set[str] = set()
            for lesson in lessons:
                for scan in self.repository.list_scans(lesson.id):
                    owner = bindings[scan.uid]
                    if owner is None:
                        unknown.add(scan.uid)
                        continue
                    expected.add(owner)
                    key = (lesson.id, owner)
                    presences[key] = min(presences.get(key, scan.first_seen), scan.first_seen)
            rows = []
            for student in self.repository.list_students():
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
    def _cell(lesson: Lesson, expected: bool, first_seen: datetime | None) -> AttendanceCell:
        if first_seen is not None:
            return AttendanceCell("present", first_seen, not expected)
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
            interrupted = [
                lesson for lesson in self.repository.list_lessons() if lesson.state == "open"
            ]
            for lesson in interrupted:
                self.repository.put_lesson(replace(lesson, state="completed"))
        return len(interrupted)

    def restore(self, path: Path) -> None:
        if any(lesson.state == "open" for lesson in self.repository.list_lessons()):
            raise AttendanceError("Перед восстановлением завершите открытую регистрацию.")
        self.repository.restore(path)
        # A backup can contain an open session; restored data never silently resumes it.
        self.recover_interrupted()

    def close(self) -> None:
        self.repository.close()
