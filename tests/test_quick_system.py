"""Real temporary file/reader/archive integration and the public capability boundary."""

from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from quick_test_support import open_backend, seed_workspace

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.operations import Operation, canonical_json
from attendance_lab.application.quick_backend import ViewContext
from attendance_lab.application.quick_system import SystemActions, local_path
from attendance_lab.domain.models import AttendanceError, PublicResult
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.presentation.quick.public_projection import PublicProjection


def invoke(backend, name, payload, identity=None):
    from uuid import uuid4

    return backend.command(Operation.create(identity or str(uuid4()), name, payload))


@pytest.fixture
def lab(tmp_path):
    path = tmp_path / "synthetic-system.sqlite3"
    ids = seed_workspace(path)
    backend = open_backend(path)
    yield backend, ids, tmp_path
    assert backend.controller.retry_pending()
    backend.close()


@pytest.mark.parametrize(
    "value", ["", "https://example.test/a.csv", "file://remote/a.csv", "smb://host/a", "file://"]
)
def test_only_reviewed_local_paths_are_accepted(value):
    with pytest.raises(AttendanceError):
        local_path(value)


def test_file_urls_backup_restore_and_exports_preserve_database_and_archive(lab):
    backend, ids, directory = lab
    backup = directory / "резервная копия.sqlite3"
    invoke(backend, "system.backup", {"path": backup.as_uri()})
    assert backup.is_file()
    backend.service.create_student("Временный После копии", student_id="later")
    invoke(backend, "system.restore", {"path": backup.as_uri()})
    assert "later" not in {student.id for student in backend.service.list_students()}
    assert backend.controller.active_session_id is None
    invoke(backend, "system.archive", {"lesson_id": ids["archive_pair"]})
    snapshot = backend.snapshot(ViewContext(ids["courses"][0], ids["lessons"][0], 1))
    assert snapshot["system"]["archive_lesson_id"] == ids["archive_pair"]
    assert len(snapshot["system"]["archive_students"]) == len(backend.service.list_students())
    canonical_json(snapshot)
    paths = {
        "system.export_log": directory / "архив журнала.csv",
        "system.export_csv": directory / "посещения.csv",
        "system.export_students": directory / "студенты.csv",
    }
    for name, path in paths.items():
        invoke(
            backend,
            name,
            {
                "path": path.as_uri(),
                "lesson_id": ids["archive_pair"],
                "discipline_id": ids["discipline"],
                "kind": "matrix",
            },
        )
        assert path.is_file() and path.read_text(encoding="utf-8-sig").strip()
    assert list(directory.glob("synthetic-system.before-restore.*.sqlite3"))


def test_archive_commands_cannot_edit_course_owned_pair_or_register_it_as_legacy(lab):
    backend, ids, _ = lab
    occurrence = backend.service.courses.get_lesson(ids["lessons"][0])
    pair = backend.service.redesign.get_block(occurrence.block_id).pair_ids[0]
    for name, payload in (
        ("system.archive", {"lesson_id": pair}),
        ("system.set_legacy_roster", {"lesson_id": pair, "student_ids": []}),
        ("registration.open_legacy", {"lesson_id": pair}),
    ):
        with pytest.raises(AttendanceError, match="архив"):
            invoke(backend, name, payload)
    assert not backend.controller.accepting


def test_legacy_creation_and_roster_are_durable_repeatable_commands(lab):
    backend, ids, _ = lab
    starts = datetime(2026, 9, 30, 10, tzinfo=UTC)
    payload = {
        "discipline_id": ids["discipline"],
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=1)).isoformat(),
        "timezone": "UTC",
        "title": "Учебное архивное занятие",
        "room": "Учебная",
    }
    first = invoke(backend, "system.save_legacy", payload, "legacy-stable")
    assert invoke(backend, "system.save_legacy", payload, "legacy-stable") == first
    lesson_id = first["lesson_id"]
    invoke(
        backend,
        "system.set_legacy_roster",
        {"lesson_id": lesson_id, "student_ids": [ids["known_student"]]},
    )
    assert backend.service.get_lesson_roster(lesson_id) == {ids["known_student"]}
    assert sum(item.id == lesson_id for item in backend.service.list_source_lessons()) == 1


def test_restore_requires_closed_registration_and_no_unconfirmed_card(lab):
    backend, ids, directory = lab
    backup = directory / "closed-copy.sqlite3"
    invoke(backend, "system.backup", {"path": str(backup)})
    session = invoke(
        backend,
        "registration.open",
        {"course_id": ids["courses"][0], "lesson_id": ids["lessons"][0]},
    )["session_id"]
    with pytest.raises(AttendanceError, match="закройте"):
        invoke(backend, "system.restore", {"path": str(backup)})
    backend.controller._capture("00AA", datetime.now(UTC))
    with pytest.raises(AttendanceError, match="предъявления"):
        backend.system.execute("system.restore", {"path": str(backup)})
    assert backend.controller.pending_count == 1
    assert backend.controller.active_session_id == session
    backend.poll()
    invoke(backend, "registration.close", {"session_id": session})
    assert backend.controller.pending_count == 0


def test_failed_restore_preserves_current_state_and_surfaces_error(lab):
    backend, ids, directory = lab
    source = directory / "not-database.sqlite3"
    source.write_text("synthetic invalid backup")
    before = {student.id for student in backend.service.list_students()}
    with pytest.raises(AttendanceError):
        invoke(backend, "system.restore", {"path": str(source)})
    assert {student.id for student in backend.service.list_students()} == before
    canonical_json(backend.snapshot(ViewContext(ids["courses"][0], ids["lessons"][0], 1)))
    assert not list(directory.glob("synthetic-system.before-restore.*.sqlite3"))


def test_real_reader_adapter_cannot_receive_synthetic_cards(lab):
    backend, _, _ = lab

    class HardwareOnly:
        def __init__(self, capture, status):
            pass

        def list_readers(self):
            return []

        def start(self, name):
            pass

        def stop(self):
            pass

    controller = AttendanceController(backend.service, HardwareOnly)
    actions = SystemActions(backend.service, controller, CsvFiles(), demo=False)
    with pytest.raises(AttendanceError, match="Имитатор"):
        actions.execute("system.simulate", {"uid": "0011"})


def test_public_projection_never_displays_private_messages_and_clears_identity():
    app = QApplication.instance() or QApplication([])
    public = PublicProjection()
    try:
        public.set_context("Учебная пара", True)
        public.display(
            PublicResult(
                "success", "UID=0011 /private/db.sqlite3", "<b>Учебный</b>", datetime.now(UTC)
            )
        )
        assert public.person == "<b>Учебный</b>"
        assert public.headline == "Отметка сохранена"
        assert "0011" not in public.detail and "/private" not in public.detail
        public.display(
            PublicResult("error", "UID=0011 /private/db.sqlite3 disk full", "Private Name")
        )
        displayed = " ".join((public.headline, public.person, public.detail))
        assert "Не удалось сохранить отметку" in displayed
        assert all(
            secret not in displayed for secret in ("0011", "Private Name", "disk full", "/private")
        )
        public.display(PublicResult("success", "", "Учебный"))
        public.set_context("Учебная пара", False)
        assert not public.person and not public.timer.isActive()
        assert public.headline == "Регистрация закрыта"
        public.set_context("Учебная пара", True)
        public.set_timeout(1)
        public.display(PublicResult("repeat", "", "Учебный"))
        QTest.qWait(1100)
        assert public.person == "" and public.headline == "Приложите карту"
    finally:
        public.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
