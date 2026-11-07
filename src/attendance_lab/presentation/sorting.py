"""Explicit multi-column ordering shared by current roster and history."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from attendance_lab.presentation.preparation import FormDialog, combo


def sort_dialog(parent, model, proxy, metadata_columns):
    dialog = FormDialog("Сортировка — приоритет полей", parent)
    fields = []
    for position in range(3):
        field = combo(
            [("Не использовать", None)]
            + [(model.headerData(i, Qt.Orientation.Horizontal), i) for i in range(metadata_columns)]
        )
        direction = combo([("По возрастанию", False), ("По убыванию", True)])
        dialog.form.addRow(f"Поле {position + 1}", field)
        dialog.form.addRow("Направление", direction)
        fields.append((field, direction))
    dialog.finish()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        proxy.set_sort_fields(
            [
                (field.currentData(), direction.currentData())
                for field, direction in fields
                if field.currentData() is not None
            ]
        )
