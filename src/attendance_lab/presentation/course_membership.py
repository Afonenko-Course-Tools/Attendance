"""Edit academic-group membership of one existing teaching line by stable IDs."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox

from attendance_lab.presentation.common import guarded, plain_label
from attendance_lab.presentation.preparation import FormDialog, checked, choices


def edit_line_groups_dialog(parent, service, course_id, roster_id, changed) -> bool:
    """Return true only after the selected line's group assignment was saved."""
    if course_id is None or roster_id is None:
        return False
    api = service.courses
    course = api.get_course(course_id)
    roster = next((item for item in api.list_rosters(course.id) if item.id == roster_id), None)
    if roster is None or course.kind == "lecture":
        return False
    dialog = FormDialog("Группы учебной линии", parent)
    dialog.form.addRow(plain_label(f"{course.name} · {roster.name}", wrap=True))
    dialog.form.addRow(
        plain_label(
            "Группы назначаются только этой учебной линии в выбранном курсе. "
            "Они применятся и к её прежним занятиям без зафиксированного состава. "
            "Ранее зафиксированные составы и сохранённые отметки сохранятся.",
            wrap=True,
        )
    )
    groups = choices(
        [
            (f"{group.specialty_name} · группа {group.number}", group.id)
            for group in service.redesign.list_groups(course.academic_year_id)
            if group.academic_year_id == course.academic_year_id
            and group.course == course.study_course
        ],
        selected=roster.group_ids,
    )
    groups.setObjectName("LineAcademicGroups")
    dialog.form.addRow("Академические группы", groups)
    dialog.finish()
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save is not None
    save.setEnabled(bool(checked(groups)))
    groups.itemChanged.connect(lambda _item: save.setEnabled(bool(checked(groups))))
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    selected_ids = checked(groups)
    if not selected_ids:
        return False

    def apply():
        api.assign_groups(course.id, roster.id, selected_ids)
        changed()
        return True

    return bool(guarded(parent, apply))
