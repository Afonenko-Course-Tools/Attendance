"""Synthetic Qt checks for assigning catalog group identities to an existing line."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QListWidget, QWidget

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
        SimpleNamespace(
            id=101, specialty_name="Тестовая специальность", number=1, academic_year_id=2, course=2
        ),
        SimpleNamespace(
            id=102, specialty_name="Другая специальность", number=1, academic_year_id=2, course=2
        ),
        SimpleNamespace(
            id=103, specialty_name="Тестовая специальность", number=1, academic_year_id=2, course=2
        ),
        SimpleNamespace(id=104, specialty_name="Чужой год", number=1, academic_year_id=1, course=2),
        SimpleNamespace(
            id=105, specialty_name="Чужой курс", number=1, academic_year_id=2, course=3
        ),
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
        listing = dialog.findChild(QListWidget, "LineAcademicGroups")
        ids = [
            listing.item(index).data(Qt.ItemDataRole.UserRole) for index in range(listing.count())
        ]
        assert ids == [101, 102, 103]
        assert listing.item(1).checkState() == Qt.CheckState.Checked
        assert listing.item(0).text() == listing.item(2).text()
        assert "Другая специальность" in listing.item(1).text()
        for index in range(listing.count()):
            listing.item(index).setCheckState(
                Qt.CheckState.Checked if ids[index] == 103 else Qt.CheckState.Unchecked
            )
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
        listing = dialog.findChild(QListWidget, "LineAcademicGroups")
        listing.item(0).setCheckState(Qt.CheckState.Checked)
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
        listing = dialog.findChild(QListWidget, "LineAcademicGroups")
        for index in range(listing.count()):
            listing.item(index).setCheckState(Qt.CheckState.Unchecked)
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FormDialog, "exec", accept_empty)
    assert not edit_line_groups_dialog(
        parent, service, course.id, roster.id, lambda: refreshes.append(True)
    )
    assert calls == [] and refreshes == []
