"""Compact lesson selectors and labels based only on stored teaching rosters."""

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QListView

from attendance_lab.domain.redesign import AcademicGroup, TeachingRoster


def roster_caption(roster: TeachingRoster, groups: Iterable[AcademicGroup]) -> str:
    """Do not derive academic groups from attendance or opaque legacy labels."""
    explicit = sorted(
        (group for group in groups if group.id in roster.group_ids),
        key=lambda group: (group.specialty_name.casefold(), group.number, group.id),
    )
    suffix = "; ".join(
        (f"{group.specialty_name} · " if group.specialty_name else "") + f"гр. {group.number}"
        for group in explicit
    )
    return roster.name + (f" · {suffix}" if suffix else "")


class LessonSelector(QComboBox):
    """A keyboard-selectable, scrollable popup that stays inside the display."""

    visible_rows = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setView(QListView(self))
        self.setStyleSheet("QComboBox { combobox-popup: 0; }")
        self.setMaxVisibleItems(self.visible_rows)
        self.setMinimumContentsLength(20)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def showPopup(self):
        row_height = max(1, self.view().sizeHintForRow(0))
        maximum = row_height * self.visible_rows + 2 * self.view().frameWidth() + 2
        self.view().setMaximumHeight(maximum)
        super().showPopup()
        popup = self.view().window()
        if popup is not self.window():
            available = self.screen().availableGeometry()
            popup.setMaximumHeight(min(maximum, available.height()))
            popup.setMaximumWidth(max(1, available.width() - 24))
            popup.resize(
                min(popup.width(), popup.maximumWidth()),
                min(popup.height(), popup.maximumHeight()),
            )
            frame = popup.frameGeometry()
            left = max(available.left(), min(frame.left(), available.right() - frame.width() + 1))
            top = max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1))
            popup.move(popup.pos().x() + left - frame.left(), popup.pos().y() + top - frame.top())
