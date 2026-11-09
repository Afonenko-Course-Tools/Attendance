"""CSV import UX, using only synthetic files and a temporary database."""

import csv

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from attendance_lab.application.service import AttendanceService
from attendance_lab.infrastructure.sqlite import SQLiteRepository
from attendance_lab.presentation.preparation import ImportPreviewDialog

HEADERS = [
    "№",
    "ФИО",
    "Специальность",
    "Специализация/профилизация",
    "Курс",
    "Группа",
    "№ зачётки",
    "Средний балл всех сессий",
]


@pytest.fixture
def importer(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = AttendanceService(SQLiteRepository(tmp_path / "synthetic.sqlite3", mode="demo"))
    dialog = ImportPreviewDialog(service, lambda: None)
    dialog.year.setValue(2026)
    yield dialog, service, app
    dialog.close()
    service.close()


def write_csv(tmp_path, rows):
    path = tmp_path / "synthetic.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows(rows)
    return path


def import_button(dialog):
    return dialog.buttons.button(QDialogButtonBox.StandardButton.Save)


def test_errors_have_own_selected_tab_and_persistent_explanation(importer, tmp_path):
    dialog, _, _ = importer
    dialog.path.setText(
        str(
            write_csv(
                tmp_path,
                [["1", "Тестов Один", "Физика", "", "3", "1", "30001", "3.5"], ["неполная строка"]],
            )
        )
    )
    dialog.review()
    assert not import_button(dialog).isEnabled()
    assert "ошиб" in dialog.status.text().lower()
    assert "3" in dialog.errors_view.item(0).text()
    assert dialog.tabs.currentWidget() is dialog.errors_view
    assert "без изменений" not in dialog.errors_view.item(0).text().lower()
    assert "ошиб" in import_button(dialog).toolTip().lower()


def test_new_course_is_ready_without_resolving_and_keeps_second_course(importer, tmp_path):
    dialog, service, _ = importer
    api = service.redesign
    plan = api.preview_import(
        HEADERS, [["1", "Тестов Второй", "Физика", "", "2", "1", "20001", "4.0"]], 2026
    )
    api.apply_import(plan)
    old = api.list_student_contexts()[0]
    dialog.path.setText(
        str(
            write_csv(
                tmp_path,
                [
                    [str(i), f"Тестов Третий {i}", "Физика", "", "3", "1", f"3000{i}", "3.5"]
                    for i in range(1, 4)
                ],
            )
        )
    )
    dialog.review()
    assert import_button(dialog).isEnabled()
    assert "импорт" in import_button(dialog).text().lower()
    assert "3" in import_button(dialog).text()
    assert not dialog.resolve_button.isEnabled()
    assert api.list_student_contexts() == [old], "Предпросмотр ничего не записывает"
    dialog.accept()
    assert len(api.list_student_contexts()) == 4
    assert next(c for c in api.list_student_contexts() if c.student_id == old.student_id) == old


def test_browse_checks_file_without_extra_step(importer, tmp_path, monkeypatch):
    from attendance_lab.presentation import import_students

    dialog, _, _ = importer
    path = write_csv(tmp_path, [["1", "Тестов Третий", "Физика", "", "3", "1", "30001", "3.5"]])
    monkeypatch.setattr(
        import_students.QFileDialog, "getOpenFileName", lambda *args: (str(path), "CSV")
    )
    dialog.browse()
    assert dialog.plan is not None and not dialog.plan.errors
    assert import_button(dialog).isEnabled()


def test_failed_recheck_clears_previously_accepted_preview(importer, tmp_path):
    dialog, _, _ = importer
    path = write_csv(tmp_path, [["1", "Тестов Третий", "Физика", "", "3", "1", "30001", "3.5"]])
    dialog.path.setText(str(path))
    dialog.review()
    assert import_button(dialog).isEnabled()
    path.write_bytes(b"\xff\xfe")
    dialog.review()
    assert dialog.plan is None
    assert not import_button(dialog).isEnabled()
    assert "кодиров" in dialog.status.text().lower()


def test_group_resolution_only_lists_matching_course_and_covers_all_rows(
    importer, tmp_path, monkeypatch
):
    from attendance_lab.presentation import import_students

    dialog, service, _ = importer
    api = service.redesign
    plan = api.preview_import(
        HEADERS,
        [
            ["1", "Тестов Второй", "Физика", "", "2", "1", "20001", "4.0"],
            ["2", "Тестов Третий", "Физика", "", "3", "1", "30001", "4.0"],
        ],
        2026,
    )
    api.apply_import(plan)
    year, specialty = api.list_years()[0], api.list_specialties()[0]
    alternate = api.save_group(year.id, specialty.id, 3, 1, "вечерняя")
    dialog.path.setText(
        str(
            write_csv(
                tmp_path,
                [
                    [str(i), f"Новый Тестов {i}", "Физика", "", "3", "1", f"3990{i}", "3.5"]
                    for i in range(1, 4)
                ],
            )
        )
    )
    dialog.review()
    assert dialog.resolve_button.isEnabled()
    conflicts = dialog.group_conflicts()
    assert len(conflicts) == 1 and conflicts[0].source_rows == (2, 3, 4)
    assert all(g.course == 3 for g in conflicts[0].candidates)

    def choose_group(resolver):
        assert len(resolver.choices) == 1
        _, choice = resolver.choices[0]
        assert choice.currentData() is None, "Решение не должно приниматься за пользователя"
        choice.setCurrentIndex(choice.findData(alternate.id))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(import_students.ImportGroupsDialog, "exec", choose_group)
    dialog.resolve()
    assert dialog.plan is not None and not dialog.plan.errors
    assert dict(dialog.plan.group_choices) == {2: alternate.id, 3: alternate.id, 4: alternate.id}
    dialog.accept()
    assert all(
        c.group_id == alternate.id
        for c in api.list_student_contexts()
        if c.record_book_number.startswith("399")
    )
