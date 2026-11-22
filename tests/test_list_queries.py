from attendance_lab.application.list_queries import StudentSearch, filter_report_rows
from attendance_lab.domain.redesign import StudentContext


def test_student_search_preserves_identity_and_uses_same_fields_for_both_views():
    students = (
        StudentContext("a", "Алёна Соколова", "001087", group_id=11, group_number=7),
        StudentContext(
            "b", "Алёна Соколова", "001088", group_id=12, group_number=7, profile="Химия"
        ),
    )
    mappings = [
        {
            "id": student.student_id,
            "name": student.full_name,
            "record_book_number": student.record_book_number,
            "group_number": student.group_number,
            "profile": student.profile,
        }
        for student in students
    ]

    assert [s.student_id for s in StudentSearch(students).find("алена 001088")] == ["b"]
    assert [s["id"] for s in StudentSearch(mappings).find("алена 001088")] == ["b"]
    assert [s.student_id for s in StudentSearch(students).find("соколова химия")] == ["b"]
    assert [s.student_id for s in StudentSearch(students).find("соколовa 001087")] == ["a"]


def test_report_table_filters_by_identity_and_sorts_dates_and_grades_by_value():
    newer = {
        "id": "a",
        "full_name": "Алёна Соколова",
        "group": "7",
        "group_id": 11,
        "lesson_id": 1,
        "lesson_starts_at": "2026-11-02T09:00:00+03:00",
        "lesson": "02.11.2026 09:00",
        "grade": 9,
    }
    older = dict(
        newer,
        id="b",
        group_id=12,
        lesson_id=2,
        lesson_starts_at="2026-10-28T15:00:00+03:00",
        lesson="28.10.2026 15:00",
        grade=0,
    )
    pending = dict(newer, id="c", lesson_id=None, lesson_starts_at=None, lesson="", grade=None)
    rows = (newer, pending, older)

    assert filter_report_rows(rows, sort="lesson") == [older, newer, pending]
    assert filter_report_rows(rows, sort="lesson", descending=True) == [newer, older, pending]
    assert filter_report_rows(rows, sort="grade") == [older, newer, pending]
    assert filter_report_rows(rows, query="алена", group_id=12, status="graded") == [older]
    assert filter_report_rows(rows, status="pending", group_id=11) == [pending]
    assert filter_report_rows(rows, lesson_id=2) == [older]
