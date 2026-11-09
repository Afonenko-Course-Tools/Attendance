"""Attendance-only courses retain an accessible flat XLSX export."""

from zipfile import ZipFile

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from test_course_workflow import course  # noqa: F401

from attendance_lab.application.controller import AttendanceController
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.presentation.admin import MainWindow


def test_java_attendance_journal_exports_without_enabling_assessment(course, tmp_path, monkeypatch):  # noqa: F811
    service, selected_course, _ = course
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "synthetic-course-export.xlsx"
    monkeypatch.setattr(
        "attendance_lab.presentation.grading.QFileDialog.getSaveFileName",
        lambda *args: (str(path), "Excel (*.xlsx)"),
    )
    window = MainWindow(
        service,
        AttendanceController(service, DemoReader),
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "synthetic-course.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    try:
        window.show()
        window.tabs.setCurrentWidget(window.course_journal)
        app.processEvents()
        assert not window.tabs.isTabVisible(window.tabs.indexOf(window.calculation_pane))
        assert not window.course_journal.isTabVisible(1)
        button = next(
            b
            for b in window.course_workspace.journal_pane.findChildren(QPushButton)
            if b.text() == "Экспорт XLSX…"
        )
        assert button.isVisibleTo(window)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        with ZipFile(path) as archive:
            assert "xl/worksheets/sheet1.xml" in archive.namelist()
            assert b"0001" in archive.read("xl/worksheets/sheet1.xml")
        assert service.grading.works(selected_course.id) == ()
        assert not service.courses.get_course(selected_course.id).reports_enabled
    finally:
        window.close()


def test_manual_confirmation_export_does_not_claim_an_actual_arrival(course):  # noqa: F811
    from datetime import date

    service, selected_course, roster = course
    occurrence = service.courses.schedule(selected_course.id, roster.id, 1, date(2026, 9, 1), 1)
    student_id = service.courses.list_students(selected_course.id)[0].student_id
    block = service.redesign.get_block(occurrence.block_id)
    service.redesign.create_manual_batch(
        {(student_id, pair) for pair in block.pair_ids}, "Подтверждение преподавателя"
    )
    headers, rows = service.grading.export_projection(selected_course.id)
    value = rows[0][next(i for i, name in enumerate(headers) if name.startswith("Тема 1:"))]
    assert "Подтверждено вручную:" in value and "Предъявление:" not in value
    assert value.count("Подтверждено вручную:") == 1
    assert not service.repository.list_scans()
