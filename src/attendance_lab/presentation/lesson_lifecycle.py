"""Reviewable deletion and restoration commands shared by both schedules."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit

from attendance_lab.presentation.common import guarded, plain_label
from attendance_lab.presentation.preparation import FormDialog, combo


def delete_lesson_dialog(parent, service, lesson_id, changed):
    if lesson_id is None:
        return
    dialog = FormDialog("Удалить выбранное занятие?", parent)
    dialog.form.addRow(plain_label(service.lifecycle.describe(lesson_id), wrap=True))
    dialog.form.addRow(
        plain_label(
            "Весь блок исчезнет из расписания, журнала посещений и списка регистрации. Исходные предъявления, ручные отметки, доклады и история сохраняются. Вернуть занятие с теми же датой и отметками можно через «Удалённые занятия…».",
            wrap=True,
        )
    )
    reason = QLineEdit("Фиктивное занятие для тестов")
    dialog.form.addRow("Причина удаления", reason)
    dialog.finish()
    button = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    button.setText("Удалить из расписания")
    if dialog.exec() == QDialog.DialogCode.Accepted:
        guarded(
            parent, lambda: (service.lifecycle.delete_lesson(lesson_id, reason.text()), changed())
        )


def restore_lessons_dialog(parent, service, changed):
    dialog = FormDialog("Удалённые занятия — восстановление", parent)
    records = service.lifecycle.list_deletions()
    deleted = combo(
        [(service.lifecycle.describe(d.lesson_ids[0]) + " · " + d.reason, d.id) for d in records]
    )
    dialog.form.addRow("Занятие", deleted)
    dialog.form.addRow(
        plain_label(
            "Занятие вернётся с прежними датой, временем, составом и сохранённой историей. Регистрация останется закрытой.",
            wrap=True,
        )
    )
    dialog.finish()
    button = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    button.setText("Восстановить занятие")
    button.setEnabled(bool(records))
    if dialog.exec() == QDialog.DialogCode.Accepted:
        guarded(
            parent, lambda: (service.lifecycle.restore_deletion(deleted.currentData()), changed())
        )
