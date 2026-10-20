"""The migration CLI must be headless, locked and non-destructive on errors."""

import os
import sqlite3
import subprocess
import sys

from redesign_test_support import create_v1_database


def run_migration(path, *args):
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    environment.pop("DISPLAY", None)
    return subprocess.run(
        [sys.executable, "-m", "attendance_lab", "--migrate-only", "--db", str(path), *args],
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )


def test_headless_cli_migrates_existing_v1_database(tmp_path):
    path = tmp_path / "real.sqlite3"
    create_v1_database(path)
    result = run_migration(path)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert list(tmp_path.glob("*.before-migration.*.sqlite3"))


def test_migration_cli_does_not_create_a_missing_database(tmp_path):
    path = tmp_path / "missing.sqlite3"
    result = run_migration(path)
    assert result.returncode != 0
    assert not path.exists()


def test_migration_cli_wrong_mode_preserves_original(tmp_path):
    path = tmp_path / "real.sqlite3"
    create_v1_database(path)
    before = path.read_bytes()
    result = run_migration(path, "--demo")
    assert result.returncode != 0
    assert path.read_bytes() == before


def test_migration_cli_respects_application_lock(tmp_path):
    from PySide6.QtCore import QLockFile

    path = tmp_path / "real.sqlite3"
    create_v1_database(path)
    lock = QLockFile(str(path) + ".lock")
    assert lock.tryLock(100)
    try:
        result = run_migration(path)
        assert result.returncode != 0
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        lock.unlock()
