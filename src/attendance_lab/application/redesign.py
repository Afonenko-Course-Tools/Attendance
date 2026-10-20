"""Catalog, semester scheduling and explicit registration use cases, without SQL."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from attendance_lab.application.ports import CardRecord
from attendance_lab.domain.models import AttendanceError, PublicResult
from attendance_lab.domain.redesign import (
    Evidence,
    ManualBatch,
    RegistrationSession,
    ScheduledBlock,
    StudentContext,
    TeachingRoster,
    display_name,
)
from attendance_lab.domain.rules import (
    normalize_uid,
    required_text,
    utc_datetime,
    validate_timezone,
)

if TYPE_CHECKING:
    pass

from attendance_lab.application.redesign_catalog import CatalogUseCases

SLOTS = (
    ("09:00", "10:20"),
    ("10:30", "11:50"),
    ("12:00", "13:20"),
    ("13:50", "15:10"),
    ("15:20", "16:40"),
    ("17:00", "18:20"),
    ("18:30", "19:50"),
    ("20:00", "21:20"),
)


class RedesignService(CatalogUseCases):
    def set_discipline_groups(
        self, discipline_id: int, academic_year_id: int, group_ids: set[int]
    ) -> None:
        self.service._discipline(discipline_id)
        if academic_year_id not in {y.id for y in self.list_years()}:
            raise AttendanceError("Учебный год не найден.")
        if not group_ids <= {g.id for g in self.list_groups(academic_year_id)}:
            raise AttendanceError("Группа относится к другому году.")
        with self.service.repository.transaction():
            self.store.rd_set_discipline_groups(discipline_id, academic_year_id, group_ids)

    def get_discipline_groups(self, discipline_id: int, academic_year_id: int) -> set[int]:
        return self.store.rd_discipline_groups(discipline_id, academic_year_id)

    def list_rosters(
        self,
        discipline_id: int | None = None,
        academic_year_id: int | None = None,
        semester: int | None = None,
    ) -> list[TeachingRoster]:
        return [
            r
            for r in self.store.rd_rosters()
            if (discipline_id is None or r.discipline_id == discipline_id)
            and (academic_year_id is None or r.academic_year_id == academic_year_id)
            and (semester is None or r.semester == semester)
        ]

    def _roster(self, roster_id: int) -> TeachingRoster:
        result = next((r for r in self.list_rosters() if r.id == roster_id), None)
        if result is None:
            raise AttendanceError("Учебный состав не найден.")
        return result

    def save_roster(
        self,
        discipline_id: int,
        academic_year_id: int,
        semester: int,
        name: str,
        kind: str,
        duration: int,
        mode: str,
        group_ids: set[int],
        student_ids: set[str],
        roster_id: int | None = None,
    ) -> TeachingRoster:
        self.service._discipline(discipline_id)
        if roster_id is not None:
            self._roster(roster_id)
        if (
            semester not in (1, 2)
            or duration not in (1, 2)
            or kind not in ("lecture", "lab", "seminar", "other")
            or mode not in ("groups", "students")
        ):
            raise AttendanceError("Неверные параметры учебного состава.")
        if kind == "lab" and duration != 2:
            raise AttendanceError("Лабораторная занимает две пары.")
        groups = [g for g in self.list_groups(academic_year_id) if g.id in group_ids]
        if not groups or len(groups) != len(group_ids) or len({g.course for g in groups}) != 1:
            raise AttendanceError("Выберите группы одного года и курса.")
        if not group_ids <= self.get_discipline_groups(discipline_id, academic_year_id):
            raise AttendanceError("Сначала зачислите группы на дисциплину.")
        if mode == "groups" and student_ids:
            raise AttendanceError("В режиме целых групп отдельные студенты не указываются.")
        if mode == "students":
            if len(groups) != 1:
                raise AttendanceError("Частичный состав относится ровно к одной группе.")
            valid = {
                c.student_id
                for c in self.list_student_contexts(academic_year_id)
                if c.group_id in group_ids
            }
            if not student_ids <= valid:
                raise AttendanceError("Студент не входит в выбранную группу.")
        value = TeachingRoster(
            roster_id or 0,
            discipline_id,
            academic_year_id,
            semester,
            required_text(display_name(name), "Название"),
            kind,
            duration,
            mode,
            tuple(sorted(group_ids)),
            tuple(sorted(student_ids)),
        )
        with self.service.repository.transaction():
            if roster_id is not None and any(
                b.roster_id == roster_id for b in self.store.rd_blocks()
            ):
                previous = self._roster(roster_id)
                dimensions = (
                    "discipline_id",
                    "academic_year_id",
                    "semester",
                    "kind",
                    "duration",
                    "mode",
                )
                if any(getattr(previous, field) != getattr(value, field) for field in dimensions):
                    raise AttendanceError(
                        "Параметры состава с запланированными блоками изменять нельзя. "
                        "Создайте новый состав или его копию; название и список участников можно изменить."
                    )
            return self.store.rd_save_roster(value)

    def copy_roster(self, roster_id: int, semester: int) -> TeachingRoster:
        r = self._roster(roster_id)
        return self.save_roster(
            r.discipline_id,
            r.academic_year_id,
            semester,
            r.name,
            r.kind,
            r.duration,
            r.mode,
            set(r.group_ids),
            set(r.student_ids),
        )

    def _roster_contexts(self, roster: TeachingRoster) -> list[StudentContext]:
        return [
            replace(c, subgroup=roster.name if roster.mode == "students" else "")
            for c in self.list_student_contexts(roster.academic_year_id)
            if c.group_id in roster.group_ids
            and (roster.mode == "groups" or c.student_id in roster.student_ids)
        ]

    def schedule_block(
        self,
        roster_id: int,
        on_date: date,
        start_slot: int,
        *,
        timezone: str = "Europe/Minsk",
        title: str = "",
        room: str = "",
        block_id: int | None = None,
    ) -> ScheduledBlock:
        roster = self._roster(roster_id)
        if (
            not isinstance(start_slot, int)
            or start_slot < 1
            or start_slot + roster.duration - 1 > 8
        ):
            raise AttendanceError("Блок выходит за доступные слоты.")
        zone = ZoneInfo(validate_timezone(timezone))
        with self.service.repository.transaction():
            old = self.get_block(block_id) if block_id is not None else None
            if old and (
                any(self.service.get_lesson(p).roster_fixed for p in old.pair_ids)
                or len(old.pair_ids) != roster.duration
            ):
                raise AttendanceError(
                    "Зафиксированный блок нельзя переносить обычным редактированием."
                )
            pairs = []
            for offset in range(roster.duration):
                start, end = SLOTS[start_slot + offset - 1]
                pair = self.service.save_lesson(
                    roster.discipline_id,
                    datetime.combine(on_date, time.fromisoformat(start), zone),
                    datetime.combine(on_date, time.fromisoformat(end), zone),
                    timezone,
                    title,
                    room,
                    old.pair_ids[offset] if old else None,
                )
                pairs.append(pair.id)
            return self.store.rd_save_block(
                ScheduledBlock(
                    block_id or 0,
                    roster_id,
                    on_date,
                    start_slot,
                    tuple(pairs),
                    title.strip(),
                    room.strip(),
                    timezone,
                )
            )

    def list_blocks(
        self, on_date: date | None = None, discipline_id: int | None = None
    ) -> list[ScheduledBlock]:
        roster_ids = {r.id for r in self.list_rosters(discipline_id)}
        return [
            b
            for b in self.store.rd_blocks()
            if (on_date is None or b.date == on_date) and b.roster_id in roster_ids
        ]

    def get_block(self, block_id: int) -> ScheduledBlock:
        result = next((b for b in self.store.rd_blocks() if b.id == block_id), None)
        if result is None:
            raise AttendanceError("Блок не найден.")
        return result

    def get_pair_contexts(self, pair_id: int) -> list[StudentContext]:
        pair = self.service.get_lesson(pair_id)
        if pair.roster_fixed:
            return self.store.rd_context_snapshot(pair_id)
        block = next((b for b in self.list_blocks() if pair_id in b.pair_ids), None)
        return self._roster_contexts(self._roster(block.roster_id)) if block else []

    def set_pair_roster(self, pair_id: int, student_ids: set[str], reason: str) -> None:
        reason = required_text(reason, "Основание")
        self.service._check_student_ids(student_ids)
        with self.service.repository.transaction():
            self.service.get_lesson(pair_id)
            block = next((b for b in self.list_blocks() if pair_id in b.pair_ids), None)
            year = self._roster(block.roster_id).academic_year_id if block else None
            contexts = [c for c in self.list_student_contexts(year) if c.student_id in student_ids]
            if any(c.group_id is None for c in contexts):
                raise AttendanceError("У студента не определён академический контекст.")
            self.store.rd_save_snapshot(pair_id, contexts, reason)
            self.service.set_lesson_roster(pair_id, student_ids)

    def _fix_pair(self, pair_id: int) -> None:
        pair = self.service.get_lesson(pair_id)
        if not pair.roster_fixed:
            contexts = self.get_pair_contexts(pair_id)
            self.store.rd_save_snapshot(pair_id, contexts)
            self.service.repository.put_lesson_roster(pair_id, {c.student_id for c in contexts})
            self.service.repository.put_lesson(replace(pair, roster_fixed=True))

    def get_session(self, session_id: int) -> RegistrationSession:
        result = next((s for s in self.store.rd_sessions() if s.id == session_id), None)
        if result is None:
            raise AttendanceError("Сеанс регистрации не найден.")
        return result

    def open_session(self, block_id: int, target_pair_ids: set[int]) -> RegistrationSession:
        block = self.get_block(block_id)
        if not target_pair_ids or not target_pair_ids <= set(block.pair_ids):
            raise AttendanceError("Выберите одну или обе пары блока.")
        with self.service.repository.transaction():
            for session in self.store.rd_sessions():
                if session.closed_at is None:
                    self.close_session(session.id)
            for lesson in self.service.list_lessons():
                if lesson.state == "open":
                    self.service.close_lesson(lesson.id)
            for pair_id in target_pair_ids:
                self._fix_pair(pair_id)
                self.service.repository.put_lesson(
                    replace(self.service.get_lesson(pair_id), state="open")
                )
            return self.store.rd_save_session(
                RegistrationSession(0, block_id, tuple(sorted(target_pair_ids)), datetime.now(UTC))
            )

    def close_session(self, session_id: int) -> None:
        with self.service.repository.transaction():
            session = self.get_session(session_id)
            if session.closed_at is None:
                self.store.rd_save_session(replace(session, closed_at=datetime.now(UTC)))
                for pair_id in session.target_pair_ids:
                    pair = self.service.get_lesson(pair_id)
                    self.service.repository.put_lesson(replace(pair, state="completed"))

    def expand_session(self, session_id: int) -> RegistrationSession:
        with self.service.repository.transaction():
            session = self.get_session(session_id)
            block = self.get_block(session.block_id)
            for pair_id in block.pair_ids:
                self._fix_pair(pair_id)
            result = replace(session, target_pair_ids=tuple(sorted(block.pair_ids)))
            if result != session:
                result = self.store.rd_save_session(result, audit=True)
                active_pairs = {
                    p
                    for s in self.store.rd_sessions()
                    if s.closed_at is None
                    for p in s.target_pair_ids
                }
                for pair_id in result.target_pair_ids:
                    self.service.repository.put_lesson(
                        replace(
                            self.service.get_lesson(pair_id),
                            state="open" if pair_id in active_pairs else "completed",
                        )
                    )
            return result

    def record_session_scan(self, session_id: int, uid: str, at: datetime) -> PublicResult:
        uid = normalize_uid(uid)
        at = utc_datetime(at)
        with self.service.repository.transaction():
            session = self.get_session(session_id)
            if session.closed_at is not None:
                raise AttendanceError("Регистрация закрыта.")
            card = self.service.repository.get_card(uid)
            if card is None:
                card = CardRecord(uid)
                self.service.repository.put_card(card)
            previous = [
                e
                for e in self.pair_evidence(session.target_pair_ids[0])
                if e.session_id == session_id and e.kind == "card"
            ]
            repeat = any(
                e.uid == uid or (card.student_id is not None and e.student_id == card.student_id)
                for e in previous
            )
            self.store.rd_add_card_evidence(session_id, uid, at)
            name = self.service._student(card.student_id).full_name if card.student_id else None
            result = PublicResult(
                "repeat" if repeat else "success",
                "Отметка уже существует" if repeat else "Отметка сохранена",
                name,
                at,
            )
        return result

    def session_manual_mark(self, session_id: int, student_id: str, reason: str) -> ManualBatch:
        reason = required_text(reason, "Основание")
        self.service._student(student_id)
        with self.service.repository.transaction():
            session = self.get_session(session_id)
            if session.closed_at:
                raise AttendanceError("Регистрация закрыта.")
            existing = any(
                b.session_id == session_id
                and b.revoked_at is None
                and any(s == student_id for s, p in b.targets)
                for b in self.list_manual_batches()
            )
            targets = (
                tuple((student_id, p) for p in session.target_pair_ids) if not existing else ()
            )
            batch = ManualBatch(0, targets, reason, datetime.now(UTC), session_id=session_id)
            return self.store.rd_save_batch(batch, (student_id,) if not existing else ())

    def create_manual_batch(self, targets: set[tuple[str, int]], reason: str) -> ManualBatch:
        reason = required_text(reason, "Основание")
        with self.service.repository.transaction():
            active_blocks = {s.block_id for s in self.store.rd_sessions() if s.closed_at is None}
            current_pairs = {
                p for b in self.list_blocks() if b.id in active_blocks for p in b.pair_ids
            }
            current_pairs.update(p.id for p in self.service.list_lessons() if p.state == "open")
            for student_id, pair_id in targets:
                self.service._student(student_id)
                pair = self.service.get_lesson(pair_id)
                if pair.ends_at > datetime.now(UTC) and pair_id not in current_pairs:
                    raise AttendanceError(
                        "Историческая отметка доступна только для прошедшей пары."
                    )
            effective = tuple(
                sorted(
                    (sid, pid)
                    for sid, pid in targets
                    if not any(e.kind == "manual" and e.active for e in self.evidence(sid, pid))
                )
            )
            scheduled_pairs = {p for block in self.list_blocks() for p in block.pair_ids}
            for pair_id in sorted({pid for _, pid in effective} & scheduled_pairs):
                # The first attendance ground fixes the expected roster as part
                # of the same transaction, even without a registration session.
                self._fix_pair(pair_id)
            return self.store.rd_save_batch(ManualBatch(0, effective, reason, datetime.now(UTC)))

    def revoke_manual_batch(self, batch_id: int) -> None:
        with self.service.repository.transaction():
            batch = next((b for b in self.list_manual_batches() if b.id == batch_id), None)
            if batch is None:
                raise AttendanceError("Пакет не найден.")
            if batch.revoked_at is None:
                self.store.rd_save_batch(replace(batch, revoked_at=datetime.now(UTC)))

    def list_manual_batches(self) -> list[ManualBatch]:
        return self.store.rd_batches()

    def evidence(self, student_id: str, pair_id: int) -> list[Evidence]:
        self.service._student(student_id)
        return [e for e in self.pair_evidence(pair_id) if e.student_id == student_id]

    def pair_evidence(self, pair_id: int) -> list[Evidence]:
        self.service.get_lesson(pair_id)
        return self.store.rd_pair_evidence(pair_id)
