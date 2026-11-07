"""Independent end-to-end acceptance checks using synthetic temporary SQLite files."""

import hashlib
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from redesign_test_support import (
    LEGACY_FIRST,
    LEGACY_LAST,
    LEGACY_STUDENT,
    LEGACY_UID,
    create_v1_database,
    logical_rows,
)

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SQLiteRepository

PRESERVED_TABLES = (
    "disciplines",
    "cards",
    "scans",
    "binding_changes",
    "discipline_roster",
    "lesson_roster",
)


def assert_preserved_legacy(service: AttendanceService, path: Path, before: dict) -> None:
    assert logical_rows(path, PRESERVED_TABLES) == before
    assert service.get_lesson_roster(41) == {LEGACY_STUDENT}
    assert service.get_lesson_roster(42) == set(), "A frozen empty historical roster stays empty"
    assert {lesson.id for lesson in service.list_lessons()} == {41, 42}
    log = service.lesson_log(41)
    assert len(log) == 1 and log[0].uid == LEGACY_UID
    assert log[0].first_seen == datetime.fromisoformat(LEGACY_FIRST)
    assert log[0].last_seen == datetime.fromisoformat(LEGACY_LAST)
    report = service.report(17)
    assert report.rows[0].student.id == LEGACY_STUDENT
    assert report.rows[0].cells[0].state == "present"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] > 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        student = connection.execute(
            "SELECT id, full_name, student_group, record_book_number FROM students"
        ).fetchone()
        assert student == (LEGACY_STUDENT, "Тестов Тест", "old opaque group", None)
        assert connection.execute("SELECT count(*) FROM annual_memberships").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM scheduled_blocks").fetchone()[0] == 0


def test_v1_upgrade_preserves_ids_evidence_empty_history_and_pre_migration_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "synthetic-v1.sqlite3"
    create_v1_database(path)
    before = logical_rows(path, PRESERVED_TABLES)
    service = AttendanceService(SQLiteRepository(path))
    try:
        assert_preserved_legacy(service, path, before)
        backups = list(tmp_path.glob("synthetic-v1.before-migration.*.sqlite3"))
        assert len(backups) == 1, "Upgrading an existing database first saves a V1 recovery copy"
        with sqlite3.connect(backups[0]) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert logical_rows(backups[0], PRESERVED_TABLES) == before
    finally:
        service.close()
    # Reopening a migrated database must not migrate again or duplicate evidence.
    service = AttendanceService(SQLiteRepository(path))
    try:
        assert_preserved_legacy(service, path, before)
        assert len(list(tmp_path.glob("synthetic-v1.before-migration.*.sqlite3"))) == 1
    finally:
        service.close()


def test_restore_v1_migrates_destination_preserves_source_and_saves_current_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic-old-backup.sqlite3"
    create_v1_database(source)
    before = logical_rows(source, PRESERVED_TABLES)
    source_digest = hashlib.sha256(source.read_bytes()).digest()
    live = tmp_path / "synthetic-live.sqlite3"
    service = AttendanceService(SQLiteRepository(live))
    try:
        service.create_student("Тест До восстановления", student_id="before-restore")
        service.restore(source)
        assert_preserved_legacy(service, live, before)
        assert hashlib.sha256(source.read_bytes()).digest() == source_digest
        safeguards = list(tmp_path.glob("synthetic-live.before-restore.*.sqlite3"))
        assert len(safeguards) == 1
        with sqlite3.connect(safeguards[0]) as connection:
            assert connection.execute("SELECT id FROM students").fetchall() == [("before-restore",)]
    finally:
        service.close()


HEADERS = [
    "Внутренний № по списку",
    "ФИО",
    "Специальность",
    "Профилизация",
    "Курс",
    "Группа",
    "Номер зачётки",
]
RELATIONAL_TABLES = (
    "students",
    "academic_years",
    "courses",
    "specialties",
    "profiles",
    "academic_groups",
    "annual_memberships",
)


def import_rows(api, rows, year=2026):
    plan = api.preview_import(HEADERS, rows, year)
    assert not plan.errors
    api.apply_import(plan)
    return api.list_student_contexts()


def test_official_import_is_idempotent_and_year_rollover_preserves_previous_membership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "synthetic-import.sqlite3"
    service = AttendanceService(SQLiteRepository(path))
    try:
        api = service.redesign
        rows = [
            ["1", "Тестов Алексей", "Тестовая информатика", "Системы", "2", "1", "00009012"],
            ["1", "Тестов Алексей", "Тестовая информатика", "Сети", "2", "1", "9012"],
            ["2", "Тестов Борис", "Тестовая информатика", "Системы", "3", "1", "003"],
            ["1", "Тестов Виктор", "Тестовая физика", "Системы", "2", "1", "004"],
        ]
        first = import_rows(api, rows)
        assert len(first) == 4
        assert len({item.student_id for item in first}) == 4, "Names and ordinal are not IDs"
        assert {item.record_book_number for item in first} == {"00009012", "9012", "003", "004"}
        before = logical_rows(path, RELATIONAL_TABLES)
        import_rows(api, rows)
        assert logical_rows(path, RELATIONAL_TABLES) == before
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT count(*) FROM academic_groups").fetchone()[0] == 3
            assert connection.execute("SELECT count(*) FROM profiles").fetchone()[0] == 3
            assert connection.execute(
                "SELECT typeof(record_book_number) FROM students"
            ).fetchall() == [
                ("text",),
                ("text",),
                ("text",),
                ("text",),
            ]
            old_memberships = connection.execute(
                "SELECT * FROM annual_memberships ORDER BY student_id"
            ).fetchall()
        next_year = [row[:4] + [str(int(row[4]) + 1)] + row[5:] for row in rows]
        import_rows(api, next_year, 2027)
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT count(*) FROM students").fetchone()[0] == 4
            assert connection.execute("SELECT count(*) FROM annual_memberships").fetchone()[0] == 8
            all_memberships = connection.execute("SELECT * FROM annual_memberships").fetchall()
            assert all(row in all_memberships for row in old_memberships)
            assert connection.execute("SELECT count(*) FROM scans").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM lesson_roster").fetchone()[0] == 0
    finally:
        service.close()


def test_invalid_and_stale_import_plans_cannot_partially_change_directory(tmp_path: Path) -> None:
    path = tmp_path / "synthetic-plans.sqlite3"
    service = AttendanceService(SQLiteRepository(path))
    try:
        api = service.redesign
        row = ["1", "Тестов Алексей", "Тестовая специальность", "", "1", "1", "00001"]
        import_rows(api, [row])
        before = logical_rows(path, RELATIONAL_TABLES)
        for bad_rows in ([row, row], [row, row[:-1] + [""]], [row, row[:-1] + ["１２３"]]):
            invalid = api.preview_import(HEADERS, bad_rows, 2026)
            assert invalid.errors
            with pytest.raises(AttendanceError):
                api.apply_import(invalid)
            assert logical_rows(path, RELATIONAL_TABLES) == before
        stale = api.preview_import(HEADERS, [row[:1] + ["Исправленное имя"] + row[2:]], 2026)
        assert not stale.errors
        newer = row[:-1] + ["00002"]
        import_rows(api, [newer])
        current = logical_rows(path, RELATIONAL_TABLES)
        with pytest.raises(AttendanceError):
            api.apply_import(stale)
        assert logical_rows(path, RELATIONAL_TABLES) == current
    finally:
        service.close()


@pytest.fixture
def lab(tmp_path: Path):
    service = AttendanceService(SQLiteRepository(tmp_path / "synthetic-lab.sqlite3"))
    try:
        api = service.redesign
        contexts = import_rows(
            api,
            [
                ["1", "Тестов Алексей", "Тестовая информатика", "", "2", "1", "00001"],
                ["2", "Тестов Борис", "Тестовая информатика", "", "2", "1", "00002"],
            ],
        )
        by_book = {item.record_book_number: item for item in contexts}
        group = api.list_groups()[0]
        discipline = service.save_discipline("Тестовая лабораторная")
        api.set_discipline_groups(discipline.id, group.academic_year_id, {group.id})
        roster = api.save_roster(
            discipline.id,
            group.academic_year_id,
            1,
            "Тестовый состав",
            "lab",
            2,
            "groups",
            {group.id},
            set(),
        )
        block = api.schedule_block(roster.id, date(2026, 9, 7), 1)
        assert len(block.pair_ids) == 2
        yield service, api, discipline, block, by_book
    finally:
        service.close()


def is_present(service, discipline_id, student_id, pair_id):
    report = service.report(discipline_id)
    column = next(i for i, lesson in enumerate(report.lessons) if lesson.id == pair_id)
    row = next((item for item in report.rows if item.student.id == student_id), None)
    return row is not None and row.cells[column].state == "present"


def test_session_expansion_retargets_own_old_new_and_late_bound_evidence_only(lab) -> None:
    service, api, discipline, block, students = lab
    first, second = block.pair_ids
    alice = students["00001"].student_id
    bob = students["00002"].student_id
    at = datetime(2026, 9, 7, 8, 45, tzinfo=UTC)  # Only one real read, during the second pair.
    original = api.open_session(block.id, {second})
    result = api.record_session_scan(original.id, "0000AABB", at)
    assert result.kind == "success" and not hasattr(result, "uid")
    assert "0000AABB" not in repr(result)
    neighbor = api.open_session(block.id, {second})
    service.bind_card("0000CCDD", bob)
    api.record_session_scan(neighbor.id, "0000CCDD", at)
    api.expand_session(original.id)  # Expanding a closed historical session is explicit.
    service.bind_card("0000AABB", alice)
    assert all(is_present(service, discipline.id, alice, pair) for pair in block.pair_ids)
    assert not is_present(service, discipline.id, bob, first)
    assert is_present(service, discipline.id, bob, second)
    assert set(api.get_session(neighbor.id).target_pair_ids) == {second}
    for pair in block.pair_ids:
        evidence = [item for item in api.evidence(alice, pair) if item.active]
        assert len(evidence) == 1 and evidence[0].at == at
        assert evidence[0].session_id == original.id
    api.close_session(neighbor.id)
    continued = api.open_session(block.id, {second})
    api.expand_session(continued.id)
    api.record_session_scan(continued.id, "0000CCDD", at + timedelta(minutes=1))
    assert is_present(service, discipline.id, bob, first)
    with sqlite3.connect(service.repository.path) as connection:
        assert connection.execute("SELECT count(*) FROM scans").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM card_evidence").fetchone()[0] == 3


def test_manual_batches_keep_exact_targets_deduplicate_and_revoke_only_owned_grounds(lab):
    service, api, discipline, block, students = lab
    first, second = block.pair_ids
    alice = students["00001"].student_id
    bob = students["00002"].student_id
    session = api.open_session(block.id, {first})
    service.bind_card("0000AABB", alice)
    api.record_session_scan(session.id, "0000AABB", datetime(2026, 9, 7, 6, 1, tzinfo=UTC))
    fixed = api.create_manual_batch({(alice, first), (bob, first)}, "Синтетическое исправление")
    assert set(fixed.targets) == {(alice, first), (bob, first)}
    duplicate = api.create_manual_batch({(alice, first), (bob, first)}, "Повтор команды")
    assert not duplicate.targets
    api.revoke_manual_batch(duplicate.id)
    assert is_present(service, discipline.id, bob, first)
    api.expand_session(session.id)
    assert not is_present(service, discipline.id, bob, second), "Historical batch stays fixed"
    api.revoke_manual_batch(fixed.id)
    assert is_present(service, discipline.id, alice, first), "Independent card ground survives"
    assert is_present(service, discipline.id, alice, second)
    assert not is_present(service, discipline.id, bob, first)
    grounds = api.evidence(alice, first)
    assert any(item.kind == "manual" and not item.active for item in grounds)
    assert any(item.kind == "card" and item.active for item in grounds)


def test_quick_mark_keeps_session_link_despite_existing_fixed_manual_ground(lab):
    service, api, discipline, block, students = lab
    first, second = block.pair_ids
    bob = students["00002"].student_id
    fixed = api.create_manual_batch({(bob, first)}, "Историческая отметка")
    session = api.open_session(block.id, {first})
    quick = api.session_manual_mark(session.id, bob, "Быстрая отметка")
    assert quick.session_id == session.id
    api.expand_session(session.id)
    assert is_present(service, discipline.id, bob, second)
    api.revoke_manual_batch(quick.id)
    assert is_present(service, discipline.id, bob, first)
    assert not is_present(service, discipline.id, bob, second)
    api.revoke_manual_batch(fixed.id)
    assert not is_present(service, discipline.id, bob, first)


def test_relational_invariants_reject_direct_sql_bypass(lab) -> None:
    service, api, _, _, _ = lab
    import_rows(
        api, [["1", "Тестов Виктор", "Другая специальность", "Чужой профиль", "2", "1", "00003"]]
    )
    other_year = api.save_year(2027)
    with sqlite3.connect(service.repository.path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        membership_id, specialty_id = connection.execute(
            "SELECT id,specialty_id FROM annual_memberships ORDER BY id LIMIT 1"
        ).fetchone()
        foreign_profile, foreign_specialty = connection.execute(
            "SELECT id,specialty_id FROM profiles WHERE specialty_id != ?", (specialty_id,)
        ).fetchone()
        group_values = connection.execute(
            "SELECT academic_year_id,specialty_id,course_id,group_number,variant_key "
            "FROM academic_groups ORDER BY id LIMIT 1"
        ).fetchone()
        assert group_values[-1] == ""
        rejected = [
            (
                "UPDATE annual_memberships SET academic_year_id=? WHERE id=?",
                (other_year.id, membership_id),
            ),
            (
                "UPDATE annual_memberships SET profile_id=? WHERE id=?",
                (foreign_profile, membership_id),
            ),
            (
                "INSERT INTO profile_aliases(specialty_id,normalized_key,profile_id) VALUES (?,?,?)",
                (specialty_id, "synthetic bad alias", foreign_profile),
            ),
            (
                "INSERT INTO academic_groups(academic_year_id,specialty_id,course_id,group_number,"
                "variant_key) VALUES (?,?,?,?,?)",
                group_values,
            ),
            (
                "INSERT INTO academic_groups(academic_year_id,specialty_id,course_id,group_number,"
                "variant_key) VALUES (?,?,?,?,?)",
                group_values[:-1] + (None,),
            ),
            (
                "UPDATE students SET record_book_number='１２３' WHERE record_book_number='00001'",
                (),
            ),
            ("UPDATE students SET record_book_number='00001' WHERE record_book_number='00002'", ()),
        ]
        assert foreign_specialty != specialty_id
        for statement, params in rejected:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, params)
            connection.rollback()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_aliases_share_canonical_namespace_and_normalize_unicode_without_fuzzy_merging(lab):
    service, api, _, _, _ = lab
    specialty = api.list_specialties()[0]
    api.add_specialty_alias(specialty.id, "  ТЕСТОВЫЙ\u00a0\u00a0АЛИАС  ")
    import_rows(api, [["1", "Тестов Виктор", "тестовый алиас", "", "2", "1", "00003"]])
    assert len(api.list_specialties()) == 1
    import_rows(api, [["1", "Тестов Григорий", "Тестовая информатка", "", "2", "1", "00004"]])
    assert len(api.list_specialties()) == 2, "A plausible typo must not auto-merge catalogs"
    other = next(item for item in api.list_specialties() if item.id != specialty.id)
    with pytest.raises(AttendanceError):
        api.add_specialty_alias(other.id, "ТЕСТОВАЯ ИНФОРМАТИКА")
    with pytest.raises(AttendanceError):
        api.rename_specialty(other.id, "тестовый алиас")
    assert len(api.list_specialties()) == 2


def test_queued_controller_scan_keeps_captured_session_across_registration_switch(lab):
    from attendance_lab.application.controller import AttendanceController

    service, api, discipline, block, students = lab
    first, second = block.pair_ids
    alice = students["00001"].student_id
    bob = students["00002"].student_id
    service.bind_card("0000AABB", alice)
    service.bind_card("0000CCDD", bob)

    class SyntheticReader:
        def __init__(self, callback, status):
            self.callback = callback

        def list_readers(self):
            return []

        def start(self, name):
            pass

        def stop(self):
            pass

    controller = AttendanceController(service, SyntheticReader)
    try:
        controller.open_session(block.id, {first})
        captured_session = controller.active_session_id
        controller.reader.callback("0000AABB", datetime(2026, 9, 7, 6, 1, tzinfo=UTC))
        controller.open_session(block.id, {second})
        next_session = controller.active_session_id
        assert captured_session != next_session
        controller.reader.callback("0000CCDD", datetime(2026, 9, 7, 8, 1, tzinfo=UTC))
        events = controller.drain()
        assert is_present(service, discipline.id, alice, first)
        assert not is_present(service, discipline.id, alice, second)
        assert not is_present(service, discipline.id, bob, first)
        assert is_present(service, discipline.id, bob, second)
        assert {item.session_id for item in api.evidence(alice, first)} == {captured_session}
        assert {item.session_id for item in api.evidence(bob, second)} == {next_session}
        scan_events = [event for event in events if event.kind == "changed" and event.scan]
        assert {event.scan.session_id for event in scan_events} == {captured_session, next_session}
        for event in events:
            if event.kind == "public" and event.result:
                assert event.scan is None
                assert not hasattr(event.result, "uid")
                assert "0000AABB" not in repr(event.result)
                assert "0000CCDD" not in repr(event.result)
    finally:
        controller.shutdown()


def test_public_display_suppresses_private_errors_and_clears_name_on_timeout():
    from PySide6.QtWidgets import QApplication, QLabel

    from attendance_lab.domain.models import PublicResult
    from attendance_lab.presentation.public import PublicWindow

    app = QApplication.instance() or QApplication([])
    window = PublicWindow()
    try:
        window.set_lesson("Тестовая лабораторная", registration_open=True)
        window.display(PublicResult("success", "", "Тестов Алексей"))
        assert "Тестов Алексей" in " ".join(label.text() for label in window.findChildren(QLabel))
        window.timer.timeout.emit()
        app.processEvents()
        assert "Тестов Алексей" not in " ".join(
            label.text() for label in window.findChildren(QLabel)
        )
        window.display(PublicResult("error", "UID=0000AABB /private/synthetic.sqlite3 SQL failed"))
        rendered = " ".join(label.text() for label in window.findChildren(QLabel))
        assert all(secret not in rendered for secret in ("0000AABB", "/private/", "SQL failed"))
        assert window.person.text() == ""
    finally:
        window.close()


def test_failed_schema_upgrade_rolls_back_every_change_and_retains_recovery_copy(tmp_path: Path):
    path = tmp_path / "synthetic-migration-failure.sqlite3"
    create_v1_database(path)
    # A preexisting colliding table forces failure after ALTER TABLE students.
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE academic_years (synthetic_collision TEXT)")
    before = logical_rows(path, PRESERVED_TABLES)
    with pytest.raises((AttendanceError, sqlite3.DatabaseError)):
        SQLiteRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        columns = {row[1] for row in connection.execute("PRAGMA table_info(students)")}
        assert "record_book_number" not in columns
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='registration_sessions'"
            ).fetchone()[0]
            == 0
        )
    assert logical_rows(path, PRESERVED_TABLES) == before
    backups = list(tmp_path.glob("synthetic-migration-failure.before-migration.*.sqlite3"))
    assert len(backups) == 1
    assert logical_rows(backups[0], PRESERVED_TABLES) == before


def test_student_correction_and_transfer_preserve_card_owner_and_frozen_pair_context(lab):
    service, api, discipline, block, students = lab
    first, second = block.pair_ids
    original = students["00001"]
    service.bind_card("0000AABB", original.student_id)
    api.open_session(block.id, {first})
    frozen_before = api.get_pair_contexts(first)
    group = api.list_groups()[0]
    moved_group = api.save_group(group.academic_year_id, group.specialty_id, group.course, 2)
    corrected = api.save_student_context(
        original.student_id,
        "Тестов Исправленный",
        "00009999",
        group.academic_year_id,
        moved_group.id,
    )
    assert corrected.student_id == original.student_id
    assert corrected.record_book_number == "00009999"
    assert service.list_cards()[0].student_id == original.student_id
    assert api.get_pair_contexts(first) == frozen_before
    historical = next(item for item in frozen_before if item.student_id == original.student_id)
    assert historical.record_book_number == "00001"
    assert historical.group_number == 1
    assert original.student_id not in {item.student_id for item in api.get_pair_contexts(second)}
    with sqlite3.connect(service.repository.path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM annual_memberships WHERE student_id=? AND academic_year_id=?",
                (original.student_id, group.academic_year_id),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM membership_changes WHERE student_id=?", (original.student_id,)
            ).fetchone()[0]
            >= 1
        )
    assert not is_present(service, discipline.id, original.student_id, first)
