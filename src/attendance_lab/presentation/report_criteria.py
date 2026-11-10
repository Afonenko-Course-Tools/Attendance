"""Discrete report rubric controls; domain criterion order stays unchanged."""

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.domain.grading import CRITERIA, Criterion


class CriterionLevels(QWidget):
    """One keyboard focus stop with large, directly clickable ordinal choices."""

    valueChanged = Signal(int)
    moveRequested = Signal(int)
    focused = Signal()

    def __init__(self, criterion: Criterion, value: int, parent=None):
        super().__init__(parent)
        self.criterion = criterion
        if not 0 <= value < criterion.steps:
            raise ValueError("Criterion level is outside its discrete scale")
        self._value = value
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(criterion.title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(6)
        title = QLabel(criterion.title)
        title.setWordWrap(True)
        self.title = title
        font = title.font()
        font.setPixelSize(14)
        title.setFont(font)
        layout.addWidget(title)
        choices = QHBoxLayout()
        choices.setSpacing(5)
        labels = (
            ("Хуже", "Нейтрально", "Лучше")
            if criterion.steps == 3
            else ("Значительно\nхуже", "Хуже", "Нейтрально", "Лучше", "Значительно\nлучше")
        )
        self.buttons = []
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for index, text in enumerate(labels):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setMinimumHeight(44)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setAccessibleName(f"{criterion.title}: {text.replace(chr(10), ' ')}")
            button.setToolTip(text.replace("\n", " ") + " · ← → изменить, ↑ ↓ другой критерий")
            self.group.addButton(button, index)
            choices.addWidget(button, 1)
            self.buttons.append(button)
        self.setValue(value)
        self.group.idClicked.connect(self._choose)
        layout.addLayout(choices)
        self.setStyleSheet(
            "QPushButton:checked { background: palette(highlight); "
            "color: palette(highlighted-text); font-weight: 600; "
            "border: 2px solid palette(highlight); }"
        )

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        if not 0 <= value < self.criterion.steps:
            raise ValueError("Criterion level is outside its discrete scale")
        self.buttons[value].setChecked(True)
        if value != self._value:
            self._value = value
            self.valueChanged.emit(value)
        self.setAccessibleDescription(self.buttons[value].text().replace("\n", " "))

    def _choose(self, value: int) -> None:
        self.setValue(value)
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            delta = -1 if event.key() == Qt.Key.Key_Left else 1
            self.setValue(max(0, min(self.criterion.steps - 1, self.value() + delta)))
            event.accept()
        elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.moveRequested.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
            event.accept()
        elif event.key() == Qt.Key.Key_Home:
            self.setValue(0)
            event.accept()
        elif event.key() == Qt.Key.Key_End:
            self.setValue(self.criterion.steps - 1)
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.focused.emit()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        font = self.title.font()
        font.setBold(False)
        self.title.setFont(font)


class ReportCriteriaPanel(QScrollArea):
    """Two collapsible rubric sections with bounded width and stable level values."""

    levelsChanged = Signal()

    def __init__(self, levels: Sequence[int] | None = None, written=False, parent=None):
        super().__init__(parent)
        initial = tuple(levels) if levels is not None else tuple(c.steps // 2 for c in CRITERIA)
        if len(initial) != len(CRITERIA):
            raise ValueError("The report rubric requires 18 levels")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(180)
        self.controls = [
            CriterionLevels(criterion, value)
            for criterion, value in zip(CRITERIA, initial, strict=True)
        ]
        self._written = None
        wrapper = QWidget()
        centered = QHBoxLayout(wrapper)
        centered.setContentsMargins(4, 0, 4, 0)
        content = QWidget()
        content.setMaximumWidth(800)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        centered.addStretch()
        centered.addWidget(content, 1)
        centered.addStretch()
        body = QVBoxLayout(content)
        body.setContentsMargins(4, 4, 4, 4)
        body.setSpacing(8)
        hint = QLabel("Нажмите нужный уровень · ← → уровень · ↑ ↓ критерий")
        hint.setWordWrap(True)
        body.addWidget(hint)
        self.oral_toggle, self.oral_body = self._section(
            body, "Устное выступление · 5 критериев", False
        )
        self.written_toggle, self.written_body = self._section(
            body, "Письменный реферат · 13 критериев", True
        )
        self.written_notice = QLabel(
            "Без реферата: эти критерии не учитываются. Выбранные уровни сохранены."
        )
        self.written_notice.setWordWrap(True)
        body.addWidget(self.written_notice)
        body.addStretch()
        self.setWidget(wrapper)
        self._navigation = [control for control in self.controls if not control.criterion.written]
        self._navigation += [control for control in self.controls if control.criterion.written]
        for control in self.controls:
            control.valueChanged.connect(lambda _value: self.levelsChanged.emit())
            control.moveRequested.connect(
                lambda direction, source=control: self._move(source, direction)
            )
            control.focused.connect(lambda source=control: self.ensureWidgetVisible(source, 0, 12))
        self.oral_toggle.setChecked(True)
        self.set_written(written)

    def _section(self, layout, title: str, written: bool):
        toggle = QToolButton()
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setMinimumHeight(36)
        toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        section = QWidget()
        rows = QVBoxLayout(section)
        rows.setContentsMargins(4, 0, 4, 0)
        rows.setSpacing(4)
        for control in self.controls:
            if control.criterion.written == written:
                rows.addWidget(control)
        section.hide()

        def expand(opened):
            section.setVisible(opened)
            toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)

        toggle.setArrowType(Qt.ArrowType.RightArrow)
        toggle.toggled.connect(expand)
        layout.addWidget(toggle)
        layout.addWidget(section)
        return toggle, section

    def set_written(self, written: bool) -> None:
        """Change participation without changing any of the 18 stored levels."""
        written = bool(written)
        self.written_body.setEnabled(written)
        self.written_notice.setVisible(not written)
        if written != self._written:
            self.written_toggle.setChecked(written)
            self._written = written

    def levels(self) -> tuple[int, ...]:
        return tuple(control.value() for control in self.controls)

    def _move(self, source: CriterionLevels, direction: int):
        index = self._navigation.index(source) + direction
        while 0 <= index < len(self._navigation):
            target = self._navigation[index]
            if target.isEnabled() and target.isVisible():
                target.setFocus(Qt.FocusReason.OtherFocusReason)
                return
            index += direction
