"""Synthetic Qt checks for assigning catalog group identities to an existing line."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QTableWidget, QWidget

from attendance_lab.domain.redesign import AcademicGroup
from attendance_lab.presentation.course_membership import edit_line_groups_dialog
from attendance_lab.presentation.preparation import FormDialog


@pytest.fixture
def membership():
    app = QApplication.instance() or QApplication([])
    course = SimpleNamespace(
        id=6, name="Учебный курс", academic_year_id=2, study_course=2, kind="seminar"
    )
    roster = SimpleNamespace(id=41, name="Учебная линия 2", group_ids=(102,))
    groups = [
        AcademicGroup(101, 2, 1, 2, 1, "", "Тестовая специальность"),
        AcademicGroup(102, 2, 2, 2, 1, "", "Другая специальность"),
        AcademicGroup(103, 2, 1, 2, 1, "Б", "Тестовая специальность"),
        AcademicGroup(104, 1, 3, 2, 1, "", "Чужой год"),
        AcademicGroup(105, 2, 4, 3, 1, "", "Чужой курс"),
    ]
    calls = []
    refreshes = []
    api = SimpleNamespace(
        get_course=lambda key: course,
        list_rosters=lambda key: [roster],
        assign_groups=lambda *args: calls.append(args),
    )
    service = SimpleNamespace(
        courses=api, redesign=SimpleNamespace(list_groups=lambda year: groups)
    )
    parent = QWidget()
    yield parent, service, course, roster, calls, refreshes
    parent.close()
    app.processEvents()


def test_assign_selected_catalog_ids_to_existing_line_and_refresh(membership, monkeypatch):
    parent, service, course, roster, calls, refreshes = membership

    def accept(dialog):
        listing = dialog.findChild(QTableWidget, "LineAcademicGroups")
        items = {
            item.data(Qt.ItemDataRole.UserRole): item
            for row in range(listing.rowCount())
            if (item := listing.item(row, 0))
        }
        assert items.keys() == {101, 102, 103}
        assert items[102].checkState() == Qt.CheckState.Checked
        assert items[101].text() == items[103].text()
        assert "Другая специальность" in items[102].text()
        for key, item in items.items():
            item.setCheckState(Qt.CheckState.Checked if key == 103 else Qt.CheckState.Unchecked)
        assert any(
            "прежним занятиям без зафиксированного состава" in label.text()
            for label in dialog.findChildren(QLabel)
        )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", accept)
    assert edit_line_groups_dialog(
        parent, service, course.id, roster.id, lambda: refreshes.append(True)
    )
    assert calls == [(6, 41, {103})]
    assert refreshes == [True]
    assert roster.name == "Учебная линия 2"


def test_cancel_does_not_assign_or_refresh(membership, monkeypatch):
    parent, service, course, roster, calls, refreshes = membership

    def cancel(dialog):
        listing = dialog.findChild(QTableWidget, "LineAcademicGroups")
        listing.item(1, 0).setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(FormDialog, "exec", cancel)
    assert not edit_line_groups_dialog(
        parent, service, course.id, roster.id, lambda: refreshes.append(True)
    )
    assert calls == [] and refreshes == []


@pytest.mark.parametrize(("course_id", "roster_id"), [(None, 41), (6, None), (6, 999)])
def test_missing_or_stale_selection_does_not_open_dialog(
    membership, monkeypatch, course_id, roster_id
):
    parent, service, _, _, calls, refreshes = membership

    def unexpected(_dialog):
        raise AssertionError("No selected line must not open a dialog")

    monkeypatch.setattr(FormDialog, "exec", unexpected)
    assert not edit_line_groups_dialog(
        parent, service, course_id, roster_id, lambda: refreshes.append(True)
    )
    assert calls == [] and refreshes == []


def test_lecture_is_not_edited_as_subgroup(membership, monkeypatch):
    parent, service, course, roster, calls, refreshes = membership
    course.kind = "lecture"
    monkeypatch.setattr(
        FormDialog, "exec", lambda _: pytest.fail("Lecture has no line-group editor")
    )
    assert not edit_line_groups_dialog(
        parent, service, course.id, roster.id, lambda: refreshes.append(True)
    )
    assert calls == [] and refreshes == []


def test_empty_group_selection_disables_save(membership, monkeypatch):
    parent, service, course, roster, calls, refreshes = membership

    def accept_empty(dialog):
        listing = dialog.findChild(QTableWidget, "LineAcademicGroups")
        row = next(
            row
            for row in range(listing.rowCount())
            if listing.item(row, 0).checkState() == Qt.CheckState.Checked
        )
        listing.selectRow(row)
        QTest.keyClick(listing, Qt.Key.Key_Delete)
        assert listing.item(row, 0).checkState() == Qt.CheckState.Unchecked
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", accept_empty)
    assert not edit_line_groups_dialog(
        parent, service, course.id, roster.id, lambda: refreshes.append(True)
    )
    assert calls == [] and refreshes == []
