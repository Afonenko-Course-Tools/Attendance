"""A physical presentation belongs to its captured occurrence, then credits one course topic."""

from datetime import UTC, date, datetime, timedelta

from test_course_workflow import course  # noqa: F401

from attendance_lab.application.controller import AttendanceController
from attendance_lab.infrastructure.readers import DemoReader


def test_one_captured_presentation_credits_whole_block_after_late_binding(course):  # noqa: F811
    service, selected_course, group_roster = course
    students = {
        s.record_book_number: s.student_id
        for s in service.courses.list_students(selected_course.id)
    }
    student_id = students["0001"]
    # The actual occurrence is held for another subgroup of the same course.
    other = service.courses.add_groups(
        selected_course.id, set(group_roster.group_ids), "Другая подгруппа", {students["0002"]}
    )
    actual = service.courses.schedule(selected_course.id, other.id, 1, date(2026, 9, 15), 1)
    expected = service.courses.schedule(selected_course.id, group_roster.id, 1, date(2026, 9, 1), 1)
    later = service.courses.schedule(selected_course.id, group_roster.id, 2, date(2026, 9, 22), 1)
    block = service.redesign.get_block(actual.block_id)
    later_block = service.redesign.get_block(later.block_id)
    controller = AttendanceController(service, DemoReader)
    controller.open_session(block.id, set(block.pair_ids))
    captured_session = controller.active_session_id
    first = datetime(2026, 9, 15, 8, 5, 7, 123456, tzinfo=UTC)
    controller._capture("00010203", first)
    # Switching the registration drains the old captured event first.
    controller.open_session(later_block.id, set(later_block.pair_ids))
    controller.drain()
    service.bind_card("00010203", student_id)
    for pair in block.pair_ids:
        evidence = service.redesign.pair_evidence(pair)
        matches = [e for e in evidence if e.active and e.student_id == student_id]
        assert len(matches) == 1 and matches[0].at == first
        assert matches[0].session_id == captured_session
    assert not service.repository.list_scans(), "No fictional scan per pair is created"
    assert service.courses.attendance(selected_course.id, student_id, expected.topic_id)
    assert not service.courses.attendance(selected_course.id, student_id, later.topic_id)
    assert not any(service.redesign.pair_evidence(p) for p in later_block.pair_ids)
    controller.shutdown()


def test_repeat_and_rebinding_keep_first_actual_time_without_cross_semester_credit(course):  # noqa: F811
    service, selected_course, roster = course
    occurrence = service.courses.schedule(selected_course.id, roster.id, 1, date(2026, 9, 1), 1)
    students = service.courses.list_students(selected_course.id)
    first = datetime(2026, 9, 1, 6, 3, tzinfo=UTC)
    session = service.courses.open_registration(occurrence.id)
    assert service.redesign.record_session_scan(session.id, "00010203", first).kind == "success"
    assert (
        service.redesign.record_session_scan(
            session.id, "00010203", first + timedelta(seconds=8)
        ).kind
        == "repeat"
    )
    service.bind_card("00010203", students[0].student_id)
    service.redesign.close_session(session.id)
    other_semester = service.courses.copy_course(
        selected_course.id, selected_course.academic_year_id, 2, "Следующий семестр"
    )
    service.bind_card(
        "00010203",
        students[1].student_id,
        correction=True,
        expected_student_id=students[0].student_id,
    )
    assert not service.courses.attendance(
        selected_course.id, students[0].student_id, occurrence.topic_id
    )
    assert service.courses.attendance(
        selected_course.id, students[1].student_id, occurrence.topic_id
    )
    assert not service.courses.attendance(
        other_semester.id,
        students[1].student_id,
        service.courses.list_topics(other_semester.id)[0].id,
    )
    pair = service.redesign.get_block(occurrence.block_id).pair_ids[0]
    times = [
        e.at
        for e in service.redesign.pair_evidence(pair)
        if e.active and e.student_id == students[1].student_id
    ]
    assert min(times) == first
