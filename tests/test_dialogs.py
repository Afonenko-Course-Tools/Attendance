"""GUI boundaries: imports apply exactly the reviewed batch and time is explicit."""

from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from attendance_lab.domain.models import Student
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.presentation.dialogs import (
    ImportStudentsDialog,
    LessonDialog,
    _qt_datetime,
)


@pytest.fixture(scope="module", autouse=True)
def gui_application():
    app = QApplication.instance() or QApplication([])
    yield app


class StudentImportService:
    def __init__(self, students=()):
        self.students = list(students)
        self.applied = []

    def list_students(self):
        return self.students

    def import_students(self, students):
        self.applied.append(students)


def test_import_preserves_reviewed_generated_ids_and_updates_by_existing_id(tmp_path):
    path = tmp_path / "students.csv"
    path.write_text(
        "student_id;ФИО;Группа\nexisting;Новое имя;12\n;Тёзка;13\n;Тёзка;13\n",
        encoding="utf-8-sig",
    )
    service = StudentImportService([Student("existing", "Старое имя", "11")])
    dialog = ImportStudentsDialog(service, CsvFiles())
    dialog.load_path(path)
    assert not service.applied
    assert dialog.updated_count == 1
    assert "Новых: 2" in dialog.summary.text()
    previewed = dialog._students
    assert len({student.id for student in previewed}) == 3
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert service.applied == [list(previewed)]
    assert dialog.imported_count == 3


def test_invalid_batch_does_not_allow_partial_import(tmp_path):
    path = tmp_path / "students.csv"
    path.write_text("id;ФИО\ns1;Имя\ns1;Другой студент\ns2;\n", encoding="utf-8")
    service = StudentImportService()
    dialog = ImportStudentsDialog(service, CsvFiles())
    dialog.load_path(path)

    assert "повтор ID s1" in dialog.errors.toPlainText()
    assert "пустое ФИО" in dialog.errors.toPlainText()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.accept()
    assert service.applied == []
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_decode_failure_requires_explicit_encoding_choice(tmp_path):
    path = tmp_path / "cp1251.csv"
    path.write_text("id;ФИО\ns1;Иван Иванов\n", encoding="cp1251")
    service = StudentImportService()
    dialog = ImportStudentsDialog(service, CsvFiles())
    dialog.load_path(path)
    assert not dialog._students
    assert not service.applied
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

    dialog.encoding.setCurrentIndex(dialog.encoding.findData("cp1251"))

    assert dialog._students == (Student("s1", "Иван Иванов"),)
    dialog.accept()
    assert service.applied == [[Student("s1", "Иван Иванов")]]


def test_malformed_csv_is_reported_without_applying_or_crashing(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_text('id;ФИО\ns1;"Незакрытая строка\n', encoding="utf-8")
    service = StudentImportService()
    dialog = ImportStudentsDialog(service, CsvFiles())
    dialog.load_path(path)
    assert dialog.errors.toPlainText()
    assert not dialog._students
    dialog.accept()
    assert not service.applied


def test_lesson_rejects_nonexistent_dst_time():
    dialog = LessonDialog(1, "Europe/Berlin")
    dialog.starts_edit.setDateTime(_qt_datetime(datetime(2026, 3, 29, 2, 30)))
    dialog.ends_edit.setDateTime(_qt_datetime(datetime(2026, 3, 29, 4, 0)))
    with pytest.raises(ValueError, match="не существует"):
        dialog.values()


def test_lesson_repeated_dst_time_has_explicit_choice_and_utc_order():
    dialog = LessonDialog(1, "Europe/Berlin")
    dialog.starts_edit.setDateTime(_qt_datetime(datetime(2026, 10, 25, 2, 30)))
    dialog.ends_edit.setDateTime(_qt_datetime(datetime(2026, 10, 25, 2, 15)))
    assert dialog.start_occurrence.count() == 2
    assert dialog.end_occurrence.count() == 2
    dialog.start_occurrence.setCurrentIndex(dialog.start_occurrence.findData(0))
    dialog.end_occurrence.setCurrentIndex(dialog.end_occurrence.findData(1))

    values = dialog.values()

    assert values["starts_at"].utcoffset() == timedelta(hours=2)
    assert values["ends_at"].utcoffset() == timedelta(hours=1)
    assert (values["ends_at"].astimezone(UTC) - values["starts_at"].astimezone(UTC)) == timedelta(
        minutes=45
    )


def test_lesson_rejects_unknown_timezone_and_end_before_start():
    dialog = LessonDialog(1, "UTC")
    dialog.timezone_edit.setText("Typo/Zone")
    with pytest.raises(ValueError, match="часовой пояс"):
        dialog.values()
    dialog.timezone_edit.setText("UTC")
    dialog.starts_edit.setDateTime(_qt_datetime(datetime(2026, 9, 7, 10, 0)))
    dialog.ends_edit.setDateTime(_qt_datetime(datetime(2026, 9, 7, 9, 0)))
    with pytest.raises(ValueError, match="позже начала"):
        dialog.values()


def test_correction_can_remove_binding_without_selecting_another_student():
    from attendance_lab.domain.models import CardInfo
    from attendance_lab.presentation.dialogs import BindingDialog

    dialog = BindingDialog(
        CardInfo("0011", "s1", "Анна", 2), [Student("s1", "Анна")], correction=True
    )
    dialog.show()
    dialog.remove_binding.setChecked(True)
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert dialog.student_id() is None
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_correction_search_down_enter_explicitly_reassigns_and_cancel_does_not_accept():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from attendance_lab.domain.models import CardInfo
    from attendance_lab.presentation.dialogs import BindingDialog

    dialog = BindingDialog(
        CardInfo("0011", "s1", "Анна", 2),
        [Student("s1", "Анна"), Student("s2", "Борис")],
        correction=True,
    )
    dialog.show()
    dialog.activateWindow()
    QApplication.processEvents()
    dialog.search.setText("Борис")
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    QTest.keyClick(dialog.search, Qt.Key.Key_Down)
    QTest.keyClick(dialog.list_widget, Qt.Key.Key_Return)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.student_id() == "s2"

    cancelled = BindingDialog(CardInfo("0011", "s1", "Анна", 2), [], correction=True)
    cancelled.remove_binding.setChecked(True)
    cancelled.reject()
    assert cancelled.result() == QDialog.DialogCode.Rejected
