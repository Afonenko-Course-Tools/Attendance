"""Synthetic regression coverage for scheduling, snapshots and evidence projections."""

from datetime import UTC, date, datetime

import pytest
from test_redesign_catalog import HEADERS

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.csv_io import cell_text
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def lab(tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "synthetic.sqlite3"))
    api = service.redesign
    plan = api.preview_import(
        HEADERS,
        [
            ["1", "Тест Один", "Тестовая специальность", "Тестовый профиль", "2", "1", "00001"],
            ["2", "Тест Два", "Тестовая специальность", "Тестовый профиль", "2", "1", "00002"],
        ],
        2026,
    )
    api.apply_import(plan)
    discipline = service.save_discipline("Тестовая дисциплина")
    group = api.list_groups()[0]
    api.set_discipline_groups(discipline.id, group.academic_year_id, {group.id})
    roster = api.save_roster(
        discipline.id,
        group.academic_year_id,
        1,
        "Лабораторная",
        "lab",
        2,
        "groups",
        {group.id},
        set(),
    )
    block = api.schedule_block(roster.id, date(2026, 9, 1), 1)
    yield service, api, discipline, roster, block
    service.close()


def test_open_block_projects_pending_and_closes_both_pairs(lab):
    service, api, discipline, _, block = lab
    api.open_session(block.id, set(block.pair_ids))
    assert [service.get_lesson(p).state for p in block.pair_ids] == ["open", "open"]
    assert {cell.state for row in service.report(discipline.id).rows for cell in row.cells} == {
        "pending"
    }
    assert service.recover_interrupted() == 1
    assert [service.get_lesson(p).state for p in block.pair_ids] == ["completed", "completed"]


def test_fixed_context_preserves_profile_group_course_and_subgroup_after_catalog_edits(lab):
    _, api, discipline, roster, block = lab
    group = api.list_groups()[0]
    student = api.list_student_contexts()[0]
    partial = api.save_roster(
        discipline.id,
        group.academic_year_id,
        1,
        "Подгруппа A",
        "lab",
        2,
        "students",
        {group.id},
        {student.student_id},
    )
    other = api.schedule_block(partial.id, date(2026, 9, 2), 3)
    api.open_session(other.id, {other.pair_ids[0]})
    before = api.get_pair_contexts(other.pair_ids[0])
    assert before[0].subgroup == "Подгруппа A"
    api.rename_profile(api.list_profiles()[0].id, "Изменённый профиль")
    api.rename_specialty(group.specialty_id, "Изменённая специальность")
    api.save_group(group.academic_year_id, group.specialty_id, 3, 9, group_id=group.id)
    assert api.get_pair_contexts(other.pair_ids[0]) == before


def test_manual_time_is_confirmation_and_card_time_takes_precedence(lab):
    service, api, discipline, _, block = lab
    student = api.list_student_contexts()[0].student_id
    api.create_manual_batch({(student, block.pair_ids[0])}, "Тестовое подтверждение")
    row = next(r for r in service.report(discipline.id).rows if r.student.id == student)
    assert row.cells[0].manual_confirmation
    assert "Подтверждено вручную" in cell_text(row.cells[0], "Europe/Minsk")
    session = api.open_session(block.id, {block.pair_ids[0]})
    service.bind_card("AABB", student)
    scan_time = datetime(2026, 9, 1, 6, 3, tzinfo=UTC)
    api.record_session_scan(session.id, "AABB", scan_time)
    row = next(r for r in service.report(discipline.id).rows if r.student.id == student)
    assert not row.cells[0].manual_confirmation
    assert row.cells[0].first_seen == scan_time
    assert service.list_cards()[0].lesson_count == 1
    assert service.lesson_counts(block.pair_ids[0]).identified_students == 1


def test_unbinding_only_removes_evidence_from_that_card(lab):
    service, api, discipline, _, block = lab
    student = api.list_student_contexts()[0].student_id
    first, second = block.pair_ids
    api.create_manual_batch({(student, second)}, "Ручное подтверждение")
    session = api.open_session(block.id, {first})
    service.bind_card("AABB", student)
    service.bind_card("CCDD", student)
    first_time = datetime(2026, 9, 1, 6, 3, tzinfo=UTC)
    other_time = datetime(2026, 9, 1, 6, 4, tzinfo=UTC)
    api.record_session_scan(session.id, "AABB", first_time)
    api.record_session_scan(session.id, "CCDD", other_time)
    scans = service.repository.list_scans()
    other_evidence = [e for e in api.evidence(student, first) if e.uid == "CCDD"]
    manual_evidence = api.evidence(student, second)
    service.unbind_card("AABB", expected_student_id=student)
    assert service.repository.list_scans() == scans
    assert api.evidence(student, first) == other_evidence
    assert api.evidence(student, second) == manual_evidence
    report = service.report(discipline.id, student_id=student)
    assert report.rows[0].cells[0].first_seen == other_time
    assert report.rows[0].cells[1].manual_confirmation


def test_slots_and_student_roster_validation(lab):
    _, api, discipline, roster, block = lab
    first, second = [api.service.get_lesson(p) for p in block.pair_ids]
    assert (first.starts_at.hour, first.ends_at.hour, first.ends_at.minute) == (6, 7, 20)
    assert (second.starts_at.hour, second.starts_at.minute) == (7, 30)
    with pytest.raises(AttendanceError):
        api.schedule_block(roster.id, date(2026, 9, 1), 8)
    group = api.list_groups()[0]
    with pytest.raises(AttendanceError):
        api.save_roster(
            discipline.id,
            group.academic_year_id,
            1,
            "Некорректный",
            "lab",
            1,
            "groups",
            {group.id},
            set(),
        )


def test_bulk_mark_accepts_exact_current_block_pairs_without_creating_scans(lab):
    service, api, _, roster, _ = lab
    today = datetime.now(UTC).date()
    block = api.schedule_block(roster.id, today, 7)
    api.open_session(block.id, {block.pair_ids[0]})
    student = api.list_student_contexts()[0].student_id
    targets = {(student, p) for p in block.pair_ids}
    batch = api.create_manual_batch(targets, "Ручной зачёт текущего блока")
    assert set(batch.targets) == targets
    assert service.repository.list_scans() == []
    assert all(
        any(e.active and e.kind == "manual" for e in api.evidence(student, p))
        for p in block.pair_ids
    )


@pytest.mark.parametrize(
    "dimension", ["discipline", "year", "semester", "kind", "duration", "mode"]
)
def test_scheduled_roster_dimensions_cannot_relabel_existing_pairs(lab, dimension):
    service, api, discipline, roster, block = lab
    group = api.list_groups()[0]
    other_discipline = service.save_discipline("Другая дисциплина")
    api.set_discipline_groups(other_discipline.id, group.academic_year_id, {group.id})
    next_year = api.save_year(2027)
    next_group = api.save_group(next_year.id, group.specialty_id, 2, 1)
    api.set_discipline_groups(discipline.id, next_year.id, {next_group.id})
    values = dict(
        discipline_id=roster.discipline_id,
        academic_year_id=roster.academic_year_id,
        semester=1,
        name=roster.name,
        kind="lab",
        duration=2,
        mode="groups",
        group_ids={group.id},
        student_ids=set(),
        roster_id=roster.id,
    )
    if dimension == "discipline":
        values["discipline_id"] = other_discipline.id
    elif dimension == "year":
        values.update(academic_year_id=next_year.id, group_ids={next_group.id})
    elif dimension == "semester":
        values["semester"] = 2
    elif dimension == "kind":
        values["kind"] = "seminar"
    elif dimension == "duration":
        values.update(kind="seminar", duration=1)
    elif dimension == "mode":
        values.update(mode="students", student_ids={api.list_student_contexts()[0].student_id})
    with pytest.raises(AttendanceError):
        api.save_roster(**values)
    assert api.get_block(block.id).roster_id == roster.id
    assert api.list_rosters(discipline.id)[0] == roster
    assert all(service.get_lesson(p).discipline_id == discipline.id for p in block.pair_ids)


def test_historical_report_group_filter_uses_latest_selected_frozen_context(lab):
    service, api, discipline, roster, block = lab
    student = api.list_student_contexts()[0]
    session = api.open_session(block.id, set(block.pair_ids))
    api.close_session(session.id)
    group = api.list_groups()[0]
    moved = api.save_group(group.academic_year_id, group.specialty_id, group.course, 2)
    api.save_student_context(
        student.student_id,
        "Изменённое имя",
        student.record_book_number,
        group.academic_year_id,
        moved.id,
    )
    old = service.report(discipline.id, group="1", student_id=student.student_id)
    assert len(old.rows) == 1
    assert old.rows[0].student.group == "1"
    assert old.rows[0].student.full_name == student.full_name
    assert not service.report(discipline.id, group="2", student_id=student.student_id).rows
    api.set_discipline_groups(discipline.id, group.academic_year_id, {group.id, moved.id})
    api.save_roster(
        discipline.id,
        group.academic_year_id,
        1,
        roster.name,
        "lab",
        2,
        "groups",
        {moved.id},
        set(),
        roster.id,
    )
    later = api.schedule_block(roster.id, date(2026, 9, 2), 1)
    later_session = api.open_session(later.id, set(later.pair_ids))
    api.close_session(later_session.id)
    entire = service.report(discipline.id, student_id=student.student_id)
    assert entire.rows[0].student.group == "2"
    previous = service.report(
        discipline.id, date_to=date(2026, 9, 1), group="1", student_id=student.student_id
    )
    assert len(previous.rows) == 1 and previous.rows[0].student.group == "1"


def test_historical_manual_batch_freezes_expected_context_before_transfer(lab):
    service, api, discipline, _, block = lab
    student = api.list_student_contexts()[0]
    pair_id = block.pair_ids[0]
    assert not service.get_lesson(pair_id).roster_fixed
    api.create_manual_batch({(student.student_id, pair_id)}, "Историческое подтверждение")
    assert service.get_lesson(pair_id).roster_fixed
    before = api.get_pair_contexts(pair_id)
    group = api.list_groups()[0]
    moved = api.save_group(group.academic_year_id, group.specialty_id, group.course, 2)
    api.save_student_context(
        student.student_id,
        student.full_name,
        student.record_book_number,
        group.academic_year_id,
        moved.id,
    )
    api.save_group(group.academic_year_id, group.specialty_id, 3, 9, group_id=group.id)
    assert api.get_pair_contexts(pair_id) == before
    report = service.report(discipline.id, student_id=student.student_id)
    assert report.rows[0].cells[0].state == "present"
    assert not report.rows[0].cells[0].outside_roster
    assert report.rows[0].student.group == "1"


def test_active_block_manual_batch_freezes_unopened_second_pair(lab):
    service, api, discipline, _, block = lab
    student = api.list_student_contexts()[0]
    first, second = block.pair_ids
    api.open_session(block.id, {first})
    assert not service.get_lesson(second).roster_fixed
    api.create_manual_batch({(student.student_id, second)}, "Зачёт второй пары блока")
    assert service.get_lesson(second).roster_fixed
    before = api.get_pair_contexts(second)
    group = api.list_groups()[0]
    moved = api.save_group(group.academic_year_id, group.specialty_id, group.course, 2)
    api.save_student_context(
        student.student_id,
        student.full_name,
        student.record_book_number,
        group.academic_year_id,
        moved.id,
    )
    api.save_group(group.academic_year_id, group.specialty_id, group.course, 7, group_id=group.id)
    assert api.get_pair_contexts(second) == before
    report = service.report(discipline.id, student_id=student.student_id)
    assert report.rows[0].cells[1].state == "present"
    assert not report.rows[0].cells[1].outside_roster
