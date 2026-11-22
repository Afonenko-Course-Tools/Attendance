"""Prepare and confirm manual journal attendance using stable identities, without UI."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.redesign import EXCUSED_ABSENCE_REASON

if TYPE_CHECKING:
    from attendance_lab.application.service import AttendanceService

MANUAL_REASONS = ("был без пропуска", "отработал с другой группой")
JOURNAL_REASONS = (*MANUAL_REASONS, EXCUSED_ABSENCE_REASON)
DEFAULT_REASON = MANUAL_REASONS[0]
type JournalCell = tuple[str, int]
type ManualTarget = tuple[int, str, int]
TOGGLE_MESSAGES = {
    "mark": "Посещение отмечено.",
    "revoke": "Ручная отметка отменена.",
    "revoke_card": "Ручная отметка отменена. Отметка по карте сохранена.",
    "revoke_partial": "Ручные отметки отменены на выбранных занятиях.",
    "revoke_partial_card": "Ручные отметки отменены на выбранных занятиях. Отметки по картам сохранены.",
    "card": "Посещение подтверждено картой. Ручной отметки для отмены нет.",
    "empty": "В выбранных ячейках нет ручных отметок для отмены.",
    "unavailable": "Для ручной отметки будущего занятия сначала откройте регистрацию.",
}


@dataclass(frozen=True, slots=True)
class JournalMarkRequest:
    course_id: int
    cells: frozenset[JournalCell]
    reason: str = DEFAULT_REASON
    toggle: bool = False
    occurrence_id: int | None = None
    revoke_only: bool = False


@dataclass(frozen=True, slots=True)
class JournalOccurrenceChoice:
    occurrence_id: int
    block_id: int
    roster_id: int
    roster_name: str
    starts_at: datetime
    ends_at: datetime
    revocations: tuple[ManualTarget, ...] = ()


@dataclass(frozen=True, slots=True)
class JournalTopicChoice:
    topic_id: int
    number: int
    title: str
    student_names: tuple[str, ...]
    occurrences: tuple[JournalOccurrenceChoice, ...]


@dataclass(frozen=True, slots=True)
class JournalTopicTarget:
    topic_id: int
    occurrence_id: int


@dataclass(frozen=True, slots=True)
class JournalMarkPreparation:
    course_id: int
    course_name: str
    cells: frozenset[JournalCell]
    topics: tuple[JournalTopicChoice, ...]
    reason: str
    mode: str = "mark"
    occurrence_id: int | None = None
    revoke_only: bool = False
    has_cards: bool = False

    @property
    def requires_choice(self) -> bool:
        return any(len(topic.occurrences) > 1 for topic in self.topics)

    @property
    def automatic_targets(self) -> tuple[JournalTopicTarget, ...]:
        return tuple(
            JournalTopicTarget(topic.topic_id, topic.occurrences[0].occurrence_id)
            for topic in self.topics
            if len(topic.occurrences) == 1
        )


@dataclass(frozen=True, slots=True)
class JournalMarkResult:
    marked_cells: frozenset[JournalCell]
    mode: str = "mark"

    @property
    def changed(self) -> bool:
        return bool(self.marked_cells)


def _validate_reason(reason: str) -> None:
    if reason not in JOURNAL_REASONS:
        raise AttendanceError("Выберите причину ручной отметки.")


def prepare_journal_mark(
    service: AttendanceService, request: JournalMarkRequest, *, exclude_excused: bool = False
) -> JournalMarkPreparation:
    """Snapshot choices; a single occurrence is automatic, multiple require an ID.

    Observed students need not belong to the course: recording presence must not
    imply enrollment. Already confirmed cells are excluded without changing them.
    """
    _validate_reason(request.reason)
    if request.toggle and request.revoke_only:
        raise AttendanceError("Выберите одно действие для отметок.")
    if request.toggle and len(request.cells) != 1:
        raise AttendanceError("Для отмены выберите одну ячейку журнала.")
    with service.repository.transaction(write=False):
        api = service.courses
        course = api.get_course(request.course_id)
        topics = {topic.id: topic for topic in api.list_topics(course.id)}
        if any(topic_id not in topics for _, topic_id in request.cells):
            raise AttendanceError("Выбранная ячейка не относится к этому курсу.")
        names = {student.id: student.full_name for student in service.list_students()}
        if any(student_id not in names for student_id, _ in request.cells):
            raise AttendanceError("Выбранный студент больше недоступен. Обновите журнал.")
        lessons = api.list_lessons(course.id) if request.cells else []
        if request.occurrence_id is not None:
            lessons = [lesson for lesson in lessons if lesson.id == request.occurrence_id]
            if not lessons or any(topic_id != lessons[0].topic_id for _, topic_id in request.cells):
                raise AttendanceError("Фактическое занятие не соответствует теме и курсу.")
        revocations: dict[int, tuple[ManualTarget, ...]] = {}
        revocable_cells = set()
        has_cards = False
        mode = "mark"
        pairs = (
            set(service.redesign.get_block(lessons[0].block_id).pair_ids)
            if request.occurrence_id is not None
            else None
        )
        if request.toggle or request.revoke_only:
            students_by_topic = defaultdict(set)
            for student_id, topic_id in request.cells:
                students_by_topic[topic_id].add(student_id)
            for lesson in lessons:
                students = students_by_topic.get(lesson.topic_id)
                if not students:
                    continue
                block = service.redesign.get_block(lesson.block_id)
                if request.revoke_only:
                    targets, card = service.redesign.manual_clear_targets(
                        {(student, pair) for student in students for pair in block.pair_ids}
                    )
                else:
                    [student_id] = students
                    evidence = service.redesign.active_attendance(student_id, block.pair_ids)
                    targets = service.redesign.manual_attendance_targets(student_id, evidence)
                    card = any(item.uid is not None for item in evidence)
                if targets:
                    revocations[lesson.id] = tuple(sorted(targets))
                    revocable_cells.update((student, lesson.topic_id) for _, student, _ in targets)
                has_cards |= card
            if revocations:
                mode = "revoke"
            elif has_cards or request.revoke_only:
                return JournalMarkPreparation(
                    course.id,
                    course.name,
                    frozenset(),
                    (),
                    request.reason,
                    "card" if has_cards else "empty",
                    request.occurrence_id,
                    request.revoke_only,
                    has_cards,
                )
        present, excused = (
            api.attendance_statuses(course.id, pairs) if mode == "mark" else (set(), set())
        )
        selected = (
            frozenset(revocable_cells) if mode == "revoke" else request.cells.difference(present)
        )
        if exclude_excused and request.reason == EXCUSED_ABSENCE_REASON:
            selected = selected.difference(excused)
        rosters = {roster.id: roster for roster in api.list_rosters(course.id)}
        choices = []
        for topic_id in sorted({key for _, key in selected}, key=lambda key: topics[key].number):
            topic = topics[topic_id]
            candidates = [
                lesson
                for lesson in lessons
                if lesson.topic_id == topic_id and (mode != "revoke" or lesson.id in revocations)
            ]
            if not candidates:
                raise AttendanceError(
                    f"Для темы {topic.number} «{topic.title}» "
                    "ещё нет занятия в расписании. Сначала запланируйте занятие."
                )
            occurrences = []
            for candidate in candidates:
                block = service.redesign.get_block(candidate.block_id)
                pairs = [service.get_lesson(pair_id) for pair_id in block.pair_ids]
                zone = ZoneInfo(block.timezone)
                occurrences.append(
                    JournalOccurrenceChoice(
                        candidate.id,
                        block.id,
                        block.roster_id,
                        rosters[block.roster_id].name,
                        min(pair.starts_at for pair in pairs).astimezone(zone),
                        max(pair.ends_at for pair in pairs).astimezone(zone),
                        revocations.get(candidate.id, ()),
                    )
                )
            choices.append(
                JournalTopicChoice(
                    topic.id,
                    topic.number,
                    topic.title,
                    tuple(sorted(names[student] for student, key in selected if key == topic_id)),
                    tuple(occurrences),
                )
            )
        return JournalMarkPreparation(
            course.id,
            course.name,
            selected,
            tuple(choices),
            request.reason,
            mode,
            request.occurrence_id,
            request.revoke_only,
            has_cards,
        )


def confirm_journal_mark(
    service: AttendanceService,
    preparation: JournalMarkPreparation,
    targets: tuple[JournalTopicTarget, ...],
    *,
    reason: str | None = None,
) -> JournalMarkResult:
    """Recheck attendance and live targets in the same transaction as the write.

    A scan received while choosing can make some or all cells a no-op. Deleting
    or retargeting an occurrence invalidates the command; it never silently picks
    a replacement. Exceptions leave the whole selection unchanged.
    """
    selected_reason = preparation.reason if reason is None else reason
    _validate_reason(selected_reason)
    target_ids = {target.topic_id: target.occurrence_id for target in targets}
    offered = {
        topic.topic_id: {occurrence.occurrence_id for occurrence in topic.occurrences}
        for topic in preparation.topics
    }
    if len(target_ids) != len(targets) or set(target_ids) != set(offered):
        raise AttendanceError("Явно выберите занятие для каждой отмечаемой темы.")
    if any(target_ids[topic_id] not in ids for topic_id, ids in offered.items()):
        raise AttendanceError("Фактическое занятие не соответствует теме и курсу.")
    if preparation.mode in ("card", "empty"):
        return JournalMarkResult(frozenset(), preparation.mode)
    with service.repository.transaction():
        if preparation.mode == "revoke":
            revocations: dict[ManualTarget, JournalCell] = {}
            inspected_pairs = set()
            for topic in preparation.topics:
                occurrence_id = target_ids[topic.topic_id]
                offered_occurrence = next(
                    item for item in topic.occurrences if item.occurrence_id == occurrence_id
                )
                lesson = service.courses.get_lesson(occurrence_id)
                if (lesson.course_id, lesson.topic_id, lesson.block_id) != (
                    preparation.course_id,
                    topic.topic_id,
                    offered_occurrence.block_id,
                ):
                    raise AttendanceError("Фактическое занятие не соответствует теме и курсу.")
                pair_ids = service.redesign.get_block(lesson.block_id).pair_ids
                inspected_pairs.update(
                    (student, pair)
                    for student, topic_id in preparation.cells
                    if topic_id == topic.topic_id
                    for pair in pair_ids
                )
                revocations.update(
                    ((batch, student, pair), (student, topic.topic_id))
                    for batch, student, pair in offered_occurrence.revocations
                    if pair in pair_ids and (student, topic.topic_id) in preparation.cells
                )
            changed = service.redesign.apply_manual_revocations(set(revocations))
            card = (preparation.revoke_only and preparation.has_cards) or any(
                item.uid is not None
                for item in service.redesign.active_attendance_in(inspected_pairs)
            )
            result_mode = (
                "revoke_partial"
                if preparation.revoke_only and preparation.requires_choice
                else "revoke"
            )
            return JournalMarkResult(
                frozenset(revocations[target] for target in changed),
                result_mode + "_card" if card else result_mode,
            )
        current = prepare_journal_mark(
            service,
            JournalMarkRequest(
                preparation.course_id,
                preparation.cells,
                selected_reason,
                occurrence_id=preparation.occurrence_id,
            ),
            exclude_excused=True,
        )
        if not current.cells:
            return JournalMarkResult(frozenset())
        for topic in current.topics:
            if target_ids.get(topic.topic_id) not in {
                occurrence.occurrence_id for occurrence in topic.occurrences
            }:
                raise AttendanceError("Фактическое занятие не соответствует теме и курсу.")
        service.courses.mark_topics(current.course_id, current.cells, target_ids, selected_reason)
        return JournalMarkResult(current.cells)
