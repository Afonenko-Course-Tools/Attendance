"""Persistence and domain acceptance scenarios, with real temporary SQLite files."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from attendance_lab.application.ports import ScanRecord
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError, Lesson, Student
from attendance_lab.infrastructure.sqlite import SQLiteRepository

NOW = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
UID = "00112233445566"


@pytest.fixture
def service(tmp_path: Path) -> Iterator[AttendanceService]:
    instance = AttendanceService(SQLiteRepository(tmp_path / "attendance.sqlite3"))
    yield instance
    instance.close()


def make_lesson(service: AttendanceService, *, hour: int = 0) -> Lesson:
    disciplines = service.list_disciplines()
    discipline = disciplines[0] if disciplines else service.save_discipline("Физика")
    return service.save_lesson(
        discipline.id,
        NOW + timedelta(hours=hour),
        NOW + timedelta(hours=hour + 1),
        "Europe/Minsk",
        title=f"Занятие {hour}",
    )


def test_manual_creation_cannot_overwrite_existing_student(service: AttendanceService) -> None:
    original = service.create_student("Тест Первый", "101", "S001")
    with pytest.raises(AttendanceError, match="уже существует"):
        service.create_student("Другой Человек", "102", " S001 ")
    assert service.list_students() == [original]


def test_two_lessons_are_distinct_sorted_and_never_selected_automatically(
    service: AttendanceService,
) -> None:
    later = make_lesson(service, hour=2)
    earlier = make_lesson(service)
    assert earlier.id != later.id
    assert [lesson.id for lesson in service.list_lessons()] == [earlier.id, later.id]
    assert all(lesson.state == "planned" for lesson in service.list_lessons())
    service.open_lesson(earlier.id)
    service.open_lesson(later.id)
    assert service.get_lesson(earlier.id).state == "completed"
    assert service.get_lesson(later.id).state == "open"


def test_unknown_card_survives_restart_and_late_binding(tmp_path: Path) -> None:
    path = tmp_path / "attendance.sqlite3"
    service = AttendanceService(SQLiteRepository(path))
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    result = service.record_scan(lesson.id, "00 11 22 33 44 55 66", NOW)
    assert result.kind == "success" and result.full_name is None
    assert UID not in repr(result)
    assert not hasattr(result, "uid")
    service.close_lesson(lesson.id)
    service.close()

    service = AttendanceService(SQLiteRepository(path))
    assert service.lesson_log(lesson.id)[0].first_seen == NOW
    assert service.report(lesson.discipline_id).unknown_count == 1
    student = service.save_student("Иванов Иван", "101")
    service.bind_card(UID, student.id)
    report = service.report(lesson.discipline_id)
    assert report.unknown_count == 0
    assert len(report.rows) == 1
    assert report.rows[0].student == student
    assert report.rows[0].cells[0].state == "present"
    assert report.rows[0].cells[0].first_seen == NOW
    assert report.rows[0].cells[0].outside_roster
    service.close()


def test_repeat_never_adds_a_row_or_changes_first_time(service: AttendanceService) -> None:
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    assert service.record_scan(lesson.id, UID, NOW).kind == "success"
    assert service.record_scan(lesson.id, UID, NOW + timedelta(minutes=4)).kind == "repeat"
    assert service.record_scan(lesson.id, UID, NOW - timedelta(minutes=2)).kind == "repeat"
    rows = service.lesson_log(lesson.id)
    assert len(rows) == 1
    assert rows[0].first_seen == NOW
    assert rows[0].last_seen == NOW + timedelta(minutes=4)


def test_lesson_counts_separate_cards_people_and_expected_roster(
    service: AttendanceService,
) -> None:
    lesson = make_lesson(service)
    expected = service.save_student("Ожидаемый студент", student_id="EXPECTED")
    service.set_discipline_roster(lesson.discipline_id, {expected.id})
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, UID, NOW)
    service.record_scan(lesson.id, "00AABBCC", NOW)
    service.record_scan(lesson.id, UID, NOW)  # Repeats at the same time cannot increase counts.
    counts = service.lesson_counts(lesson.id)
    assert counts.expected_students == 1
    assert counts.unique_cards == 2
    assert counts.identified_students == 0
    assert counts.unidentified_cards == 2

    actual = service.save_student("Другой студент", student_id="ACTUAL")
    service.close_lesson(lesson.id)
    service.bind_card(UID, actual.id)
    assert service.lesson_counts(lesson.id).unidentified_cards == 1
    service.bind_card("00AABBCC", actual.id)
    counts = service.lesson_counts(lesson.id)
    assert counts.unique_cards == 2
    assert counts.identified_students == 1  # Two cards do not become two people.
    assert counts.unidentified_cards == 0
    assert counts.expected_students == 1
    assert service.get_lesson_roster(lesson.id) == {expected.id}


def test_two_cards_one_student_one_presence_with_earliest_time(
    service: AttendanceService,
) -> None:
    lesson = make_lesson(service)
    student = service.save_student("Петров Пётр")
    service.bind_card(UID, student.id)
    service.bind_card("0001", student.id)
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, UID, NOW + timedelta(seconds=5))
    result = service.record_scan(lesson.id, "0001", NOW)
    assert result.kind == "repeat"
    assert result.full_name == student.full_name
    assert len(service.lesson_log(lesson.id)) == 2
    report = service.report(lesson.discipline_id)
    assert len(report.rows) == 1
    assert report.rows[0].cells[0].first_seen == NOW


def test_explicit_correction_reassigns_history_and_is_audited(
    service: AttendanceService,
    tmp_path: Path,
) -> None:
    first = make_lesson(service)
    second = make_lesson(service, hour=2)
    alice = service.save_student("Анна")
    bob = service.save_student("Борис")
    for lesson in (first, second):
        service.open_lesson(lesson.id)
        service.record_scan(lesson.id, UID, lesson.starts_at)
    service.bind_card(UID, alice.id)
    original = service.lesson_log(first.id)[0]
    with pytest.raises(AttendanceError, match="уже сопоставлена"):
        service.bind_card(UID, bob.id)
    assert service.report(first.discipline_id).rows[0].student.id == alice.id
    assert service.list_cards()[0].lesson_count == 2
    service.bind_card(UID, bob.id, correction=True)
    report = service.report(first.discipline_id)
    assert len(report.rows) == 1 and report.rows[0].student.id == bob.id
    assert [cell.state for cell in report.rows[0].cells] == ["present", "present"]
    corrected = service.lesson_log(first.id)[0]
    assert (corrected.uid, corrected.first_seen, corrected.last_seen) == (
        original.uid,
        original.first_seen,
        original.last_seen,
    )
    with sqlite3.connect(tmp_path / "attendance.sqlite3") as connection:
        changes = connection.execute(
            "SELECT previous_student_id, student_id, correction, scope "
            "FROM binding_changes ORDER BY id"
        ).fetchall()
    assert changes == [(None, alice.id, 0, "all_history"), (alice.id, bob.id, 1, "all_history")]


def test_roster_freezes_on_open_and_can_be_assigned_after_completion(
    service: AttendanceService,
) -> None:
    first = make_lesson(service)
    second = make_lesson(service, hour=2)
    alice = service.save_student("Анна")
    bob = service.save_student("Борис")
    outsider = service.save_student("Вера")
    service.set_discipline_roster(first.discipline_id, {alice.id})
    service.open_lesson(first.id)
    service.set_discipline_roster(first.discipline_id, {bob.id})
    assert service.get_lesson_roster(first.id) == {alice.id}
    assert service.get_lesson_roster(second.id) == {bob.id}
    service.close_lesson(first.id)
    report = service.report(first.discipline_id)
    rows = {row.student.id: row for row in report.rows}
    assert [cell.state for cell in rows[alice.id].cells] == ["missing", "outside"]
    assert [cell.state for cell in rows[bob.id].cells] == ["outside", "expected"]
    assert outsider.id not in rows
    service.set_lesson_roster(first.id, {bob.id})
    rows = service.report(first.discipline_id).rows
    assert len(rows) == 1 and rows[0].student.id == bob.id
    assert rows[0].cells[0].state == "missing"
    service.open_lesson(first.id)
    assert service.report(first.discipline_id).rows[0].cells[0].state == "pending"


def test_empty_roster_does_not_make_every_imported_student_absent(
    service: AttendanceService,
) -> None:
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, UID, NOW)
    service.close_lesson(lesson.id)
    service.import_students([Student("101", "Анна"), Student("102", "Борис")])
    assert service.report(lesson.discipline_id).rows == ()
    service.bind_card(UID, "101")
    service.set_lesson_roster(lesson.id, {"101", "102"})
    report = service.report(lesson.discipline_id)
    assert [row.cells[0].state for row in report.rows] == ["present", "missing"]
    assert not report.rows[0].cells[0].outside_roster


def test_closed_lesson_rejects_scan_and_can_be_explicitly_reopened(
    service: AttendanceService,
) -> None:
    lesson = make_lesson(service)
    with pytest.raises(AttendanceError, match="закрыта"):
        service.record_scan(lesson.id, UID, NOW)
    service.open_lesson(lesson.id)
    service.close_lesson(lesson.id)
    with pytest.raises(AttendanceError):
        service.record_scan(lesson.id, UID, NOW)
    assert not service.list_cards()
    service.open_lesson(lesson.id)
    assert service.record_scan(lesson.id, UID, NOW).kind == "success"


def test_import_rejects_invalid_data_atomically_and_updates_by_id(
    service: AttendanceService,
) -> None:
    service.save_student("Прежнее имя", "100", "x")
    with pytest.raises(AttendanceError):
        service.import_students([Student("x", "Новое имя"), Student("y", " ")])
    assert service.list_students() == [Student("x", "Прежнее имя", "100")]
    with pytest.raises(AttendanceError, match="повторяющиеся"):
        service.import_students([Student("x", "A"), Student(" x ", "B")])
    service.import_students([Student("x", "Новое имя", "101"), Student("y", "Новое имя")])
    assert len(service.list_students()) == 2
    assert next(s for s in service.list_students() if s.id == "x").group == "101"


def test_storage_failure_rolls_back_card_and_scan_without_success(tmp_path: Path) -> None:
    class FailingRepository(SQLiteRepository):
        def put_scan(self, scan: ScanRecord) -> None:
            super().put_scan(scan)
            raise sqlite3.OperationalError("simulated disk full")

    service = AttendanceService(FailingRepository(tmp_path / "failed.sqlite3"))
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    with pytest.raises(sqlite3.OperationalError):
        service.record_scan(lesson.id, UID, NOW)
    assert not service.list_cards()
    assert not service.lesson_log(lesson.id)
    service.close()


def test_commit_failure_cannot_return_public_success(tmp_path: Path) -> None:
    class FailingCommitRepository(SQLiteRepository):
        fail = False

        @contextmanager
        def transaction(self, *, write: bool = True) -> Iterator[None]:
            with super().transaction(write=write):
                yield
                if self.fail and write:
                    raise sqlite3.OperationalError("simulated commit failure")

    repository = FailingCommitRepository(tmp_path / "failed.sqlite3")
    service = AttendanceService(repository)
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    repository.fail = True
    with pytest.raises(sqlite3.OperationalError):
        service.record_scan(lesson.id, UID, NOW)
    assert not service.lesson_log(lesson.id)
    service.close()


def test_roster_validation_preserves_previous_roster(service: AttendanceService) -> None:
    lesson = make_lesson(service)
    student = service.save_student("Анна")
    service.set_discipline_roster(lesson.discipline_id, {student.id})
    with pytest.raises(AttendanceError):
        service.set_discipline_roster(lesson.discipline_id, {"missing"})
    assert service.get_discipline_roster(lesson.discipline_id) == {student.id}


def test_dates_are_utc_normalized_but_report_filter_uses_lesson_timezone(
    service: AttendanceService,
) -> None:
    discipline = service.save_discipline("Оптика")
    start = datetime(2026, 9, 8, 0, 30, tzinfo=timezone(timedelta(hours=3)))
    lesson = service.save_lesson(discipline.id, start, start + timedelta(hours=1), "Europe/Minsk")
    student = service.save_student("Анна", "101")
    service.set_discipline_roster(discipline.id, {student.id})
    assert lesson.starts_at == datetime(2026, 9, 7, 21, 30, tzinfo=UTC)
    assert not service.report(discipline.id, date_to=date(2026, 9, 7)).lessons
    report = service.report(discipline.id, date_from=date(2026, 9, 8), group="101")
    assert len(report.lessons) == 1 and len(report.rows) == 1
    assert not service.report(discipline.id, group="102").rows
    assert not service.report(discipline.id, student_id="other").rows
    with pytest.raises(AttendanceError):
        service.save_lesson(discipline.id, start.replace(tzinfo=None), start, "UTC")
    with pytest.raises(AttendanceError):
        service.save_lesson(discipline.id, start, start, "UTC")
    with pytest.raises(AttendanceError):
        service.save_lesson(discipline.id, start, start + timedelta(hours=1), "Bad/Zone")


def test_lesson_edits_do_not_move_existing_scans(service: AttendanceService) -> None:
    lesson = make_lesson(service)
    other_discipline = service.save_discipline("Математика")
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, UID, NOW)
    with pytest.raises(AttendanceError, match="переносить"):
        service.save_lesson(
            other_discipline.id, NOW, NOW + timedelta(hours=1), "UTC", lesson_id=lesson.id
        )
    service.save_lesson(
        lesson.discipline_id,
        NOW + timedelta(hours=5),
        NOW + timedelta(hours=6),
        "UTC",
        lesson_id=lesson.id,
    )
    assert service.lesson_log(lesson.id)[0].first_seen == NOW


def test_backup_restores_consistent_data_and_preserves_pre_restore_copy(
    service: AttendanceService,
    tmp_path: Path,
) -> None:
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, UID, NOW)
    backup = tmp_path / "copy.sqlite3"
    service.backup(backup)
    service.close_lesson(lesson.id)
    service.save_student("После копирования")
    service.restore(backup)
    assert not service.list_students()
    assert service.lesson_log(lesson.id)[0].uid == UID
    assert service.get_lesson(lesson.id).state == "completed"
    safeguards = list(tmp_path.glob("attendance.before-restore.*.sqlite3"))
    assert len(safeguards) == 1
    prior = AttendanceService(SQLiteRepository(safeguards[0]))
    assert prior.list_students()[0].full_name == "После копирования"
    prior.close()


def test_mode_marker_blocks_accidental_real_demo_mix_and_wrong_restore(tmp_path: Path) -> None:
    real_path = tmp_path / "real.sqlite3"
    real = AttendanceService(SQLiteRepository(real_path))
    real.save_student("Реальный студент")
    demo = AttendanceService(SQLiteRepository(tmp_path / "demo.sqlite3", mode="demo"))
    demo.save_student("Тестовый студент")
    with pytest.raises(AttendanceError, match="Режимы"):
        SQLiteRepository(real_path, mode="demo")
    with pytest.raises(AttendanceError, match="Режимы"):
        real.restore(tmp_path / "demo.sqlite3")
    assert real.list_students()[0].full_name == "Реальный студент"
    real.close()
    demo.close()


def test_restore_rejects_corruption_and_same_file_without_changing_live_data(
    service: AttendanceService,
    tmp_path: Path,
) -> None:
    service.save_student("Анна")
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"This is not a database")
    with pytest.raises(AttendanceError):
        service.restore(corrupt)
    assert service.list_students()[0].full_name == "Анна"
    with pytest.raises(AttendanceError):
        service.backup(tmp_path / "attendance.sqlite3")


@pytest.mark.parametrize("uid", ["", "0", "0x1234", "12GG", "XYZ", "---"])
def test_invalid_uids_are_rejected_without_card_rows(
    service: AttendanceService,
    uid: str,
) -> None:
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    with pytest.raises(AttendanceError):
        service.record_scan(lesson.id, uid, NOW)
    assert not service.list_cards()


def test_uid_length_and_leading_zeroes_are_preserved(service: AttendanceService) -> None:
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, "00:ab:cd:ef:01:23:45:67:89:ab", NOW)
    assert service.lesson_log(lesson.id)[0].uid == "00ABCDEF0123456789AB"


def test_explicit_startup_recovery_closes_interrupted_lesson_atomically(tmp_path: Path) -> None:
    path = tmp_path / "attendance.sqlite3"
    first = AttendanceService(SQLiteRepository(path))
    lesson = make_lesson(first)
    first.open_lesson(lesson.id)
    first.record_scan(lesson.id, UID, NOW)
    first.close()  # Simulate process exit without the controller's graceful lesson closure.
    restarted = AttendanceService(SQLiteRepository(path))
    assert restarted.get_lesson(lesson.id).state == "open"
    assert restarted.recover_interrupted() == 1
    assert restarted.get_lesson(lesson.id).state == "completed"
    assert restarted.lesson_log(lesson.id)[0].first_seen == NOW
    assert restarted.recover_interrupted() == 0
    with pytest.raises(AttendanceError):
        restarted.record_scan(lesson.id, "0002", NOW)
    restarted.close()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_backup_cannot_overwrite_live_sqlite_sidecar_files(
    service: AttendanceService,
    tmp_path: Path,
    suffix: str,
) -> None:
    service.save_student("Анна")
    with pytest.raises(AttendanceError):
        service.backup(tmp_path / f"attendance.sqlite3{suffix}")
    assert service.list_students()[0].full_name == "Анна"


def test_unbind_preserves_scans_and_audits_once(service: AttendanceService, tmp_path: Path):
    student = service.create_student("Тест", student_id="owner")
    lesson = make_lesson(service)
    service.open_lesson(lesson.id)
    service.record_scan(lesson.id, UID, NOW)
    service.record_scan(lesson.id, UID, NOW + timedelta(minutes=4))
    service.bind_card(UID, student.id)
    before = service.repository.list_scans()
    service.unbind_card("00:11:22:33:44:55:66", expected_student_id=student.id)
    service.unbind_card(UID, expected_student_id=student.id)
    assert service.repository.get_card(UID).student_id is None
    assert service.repository.list_scans() == before
    assert service.report(lesson.discipline_id).unknown_count == 1
    with sqlite3.connect(tmp_path / "attendance.sqlite3") as connection:
        assert connection.execute(
            "SELECT previous_student_id, student_id, correction, scope "
            "FROM binding_changes ORDER BY id"
        ).fetchall() == [(None, "owner", 0, "all_history"), ("owner", None, 1, "all_history")]


def test_binding_corrections_reject_stale_owner_without_changes(service: AttendanceService):
    first = service.create_student("Первый", student_id="first")
    second = service.create_student("Второй", student_id="second")
    service.bind_card(UID, second.id)
    with pytest.raises(AttendanceError):
        service.unbind_card(UID, expected_student_id=first.id)
    with pytest.raises(AttendanceError):
        service.bind_card(UID, first.id, correction=True, expected_student_id=first.id)
    assert service.repository.get_card(UID).student_id == second.id
    service.bind_card(UID, first.id, correction=True, expected_student_id=second.id)
    assert service.repository.get_card(UID).student_id == first.id
    with pytest.raises(AttendanceError):
        service.unbind_card("FFEE", expected_student_id=first.id)


def test_unbind_rolls_back_owner_when_audit_write_fails(tmp_path: Path):
    class FailingAudit(SQLiteRepository):
        fail = False

        def add_binding_change(self, *args, **kwargs):
            super().add_binding_change(*args, **kwargs)
            if self.fail:
                raise sqlite3.OperationalError("synthetic audit failure")

    repository = FailingAudit(tmp_path / "synthetic.sqlite3")
    service = AttendanceService(repository)
    try:
        service.create_student("Первый", student_id="first")
        service.bind_card(UID, "first")
        repository.fail = True
        with pytest.raises(sqlite3.OperationalError):
            service.unbind_card(UID, expected_student_id="first")
        assert repository.get_card(UID).student_id == "first"
        with sqlite3.connect(repository.path) as connection:
            assert connection.execute("SELECT count(*) FROM binding_changes").fetchone()[0] == 1
    finally:
        service.close()
