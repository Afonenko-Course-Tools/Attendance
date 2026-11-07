"""Operator name matching must tolerate typing errors without merging identities."""

from attendance_lab.domain.redesign import StudentContext
from attendance_lab.presentation.attendance_models import StudentSearch


def test_finder_tolerates_transposition_and_independent_name_tokens():
    index = StudentSearch(
        [
            StudentContext("one", "Семёнов Иван Петрович", "00001"),
            StudentContext("two", "Иванов Пётр Семёнович", "00002"),
        ]
    )
    assert index.find("  ИВАН   семенво ")[0].student_id == "one"
    assert index.find("петрович семенов")[0].student_id == "one"
    assert index.find("00002")[0].student_id == "two"
    assert index.find("семенов совершеннонесуществующий") == []


def test_same_name_results_keep_distinct_student_ids():
    index = StudentSearch(
        [
            StudentContext("one", "Иванов Иван", "001", group_number=2),
            StudentContext("two", "Иванов Иван", "002", group_number=10),
        ]
    )
    assert {s.student_id for s in index.find("иванов иван")} == {"one", "two"}
    assert index.find("002")[0].student_id == "two"
