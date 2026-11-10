"""A discrete rubric stays usable without drag precision or a large screen."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from attendance_lab.domain.grading import CRITERIA
from attendance_lab.presentation.report_criteria import ReportCriteriaPanel
from attendance_lab.presentation.theme import apply_light_theme


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    apply_light_theme(app)
    return app


def test_clicking_discrete_choices_changes_only_that_criterion(app):
    panel = ReportCriteriaPanel(written=True)
    panel.resize(720, 450)
    panel.show()
    app.processEvents()
    before = panel.levels()
    changes = []
    panel.levelsChanged.connect(lambda: changes.append(panel.levels()))
    oral = panel.controls[13]
    QTest.mouseClick(oral.buttons[4], Qt.MouseButton.LeftButton)
    assert panel.levels() == before[:13] + (4,) + before[14:]
    assert oral.buttons[4].isChecked() and oral.hasFocus()
    assert len(changes) == 1
    QTest.mouseClick(oral.buttons[4], Qt.MouseButton.LeftButton)
    assert len(changes) == 1, "Re-selecting the same level does not create a new draft edit"
    written = panel.controls[0]
    written.setValue(0)
    assert written.value() == 0 and len(written.buttons) == 3
    assert sum(button.isChecked() for button in written.buttons) == 1
    assert written.accessibleDescription() == "Хуже"


def test_written_disclosure_and_participation_preserve_all_18_levels(app):
    original = tuple(index % criterion.steps for index, criterion in enumerate(CRITERIA))
    panel = ReportCriteriaPanel(original, written=True)
    panel.show()
    app.processEvents()
    changes = []
    panel.levelsChanged.connect(lambda: changes.append(True))
    panel.set_written(False)
    assert panel.oral_toggle.isChecked() and not panel.written_toggle.isChecked()
    assert not panel.written_body.isVisible()
    assert sum(control.isEnabled() for control in panel.controls) == 5
    panel.written_toggle.click()
    assert panel.written_body.isVisible(), "The saved disabled levels remain inspectable"
    QTest.mouseClick(panel.controls[1].buttons[0], Qt.MouseButton.LeftButton)
    assert panel.levels() == original and not changes
    panel.set_written(True)
    assert panel.written_body.isVisible()
    assert all(control.isEnabled() for control in panel.controls)
    assert panel.levels() == original and not changes
    panel.written_toggle.click()
    panel.set_written(True)
    assert not panel.written_body.isVisible(), "Preview refresh must not undo user's disclosure"


def test_keyboard_changes_levels_and_moves_in_visible_section_order(app):
    panel = ReportCriteriaPanel(written=True)
    panel.resize(700, 340)
    panel.show()
    app.processEvents()
    first = panel.controls[13]
    first.setFocus()
    QTest.keyClick(first, Qt.Key.Key_Right)
    assert first.value() == 3
    QTest.keyClick(first, Qt.Key.Key_Down)
    assert panel.controls[14].hasFocus()
    panel.controls[17].setFocus()
    QTest.keyClick(panel.controls[17], Qt.Key.Key_Down)
    assert panel.controls[0].hasFocus(), (
        "Visual order is oral first, domain storage order unchanged"
    )
    QTest.keyClick(panel.controls[0], Qt.Key.Key_End)
    assert panel.controls[0].value() == 2
    QTest.keyClick(panel.controls[0], Qt.Key.Key_Home)
    assert panel.controls[0].value() == 0
    panel.set_written(False)
    panel.controls[17].setFocus()
    QTest.keyClick(panel.controls[17], Qt.Key.Key_Down)
    assert panel.controls[17].hasFocus(), "Hidden written criteria are not keyboard targets"


@pytest.mark.parametrize("width", [640, 750, 1200])
def test_rubric_width_and_scroll_keep_buttons_inside_viewport(app, width):
    panel = ReportCriteriaPanel(written=True)
    panel.resize(width, 360)
    panel.show()
    app.processEvents()
    assert panel.horizontalScrollBar().maximum() == 0
    assert panel.verticalScrollBar().maximum() > 0
    for control in panel.controls:
        assert control.width() <= 800
        for button in control.buttons:
            assert button.height() >= 44
            assert (
                button.width()
                >= button.fontMetrics().horizontalAdvance(max(button.text().splitlines(), key=len))
                + 8
            )
            assert control.rect().contains(button.geometry())
    panel.controls[12].setFocus()
    app.processEvents()
    assert panel.verticalScrollBar().value() > 0
    point = panel.controls[12].mapTo(panel.viewport(), panel.controls[12].rect().bottomRight())
    assert point.y() <= panel.viewport().height(), (
        "Keyboard focus scrolls the whole criterion into view"
    )


def _contrast(first, second):
    from PySide6.QtGui import QColor

    def luminance(value):
        color = QColor(value)
        channels = [channel / 255 for channel in (color.red(), color.green(), color.blue())]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_level_colors_have_readable_contrast_in_every_display_state():
    from attendance_lab.presentation.report_criteria import (
        DISABLED_SELECTED,
        DISABLED_TEXT,
        DISABLED_TINT,
        LEVEL_COLORS,
    )

    for colors in LEVEL_COLORS:
        assert _contrast(colors.text, colors.tint) >= 4.5
        assert _contrast(colors.text, colors.hover) >= 4.5
        assert _contrast("#ffffff", colors.selected) >= 4.5
    assert _contrast(DISABLED_TEXT, DISABLED_TINT) >= 4.5
    assert _contrast(DISABLED_TEXT, DISABLED_SELECTED) >= 4.5


def _button_background(button):
    # Sample inside the left edge, away from the text, rounded corner and border.
    return button.grab().toImage().pixelColor(10, button.height() // 2).name()


def test_rendered_ordinal_colors_checked_hover_focus_and_inactive_written(app):
    from PySide6.QtCore import QPoint

    from attendance_lab.presentation.report_criteria import (
        DISABLED_SELECTED,
        DISABLED_TINT,
        LEVEL_COLORS,
    )

    panel = ReportCriteriaPanel(written=True)
    panel.resize(780, 720)
    panel.show()
    app.processEvents()
    oral = panel.controls[13]
    oral.clearFocus()
    panel.oral_toggle.setFocus()
    app.processEvents()
    for index, button in enumerate(oral.buttons):
        expected = (
            LEVEL_COLORS[index].selected if index == oral.value() else LEVEL_COLORS[index].tint
        )
        assert _button_background(button) == expected
    QTest.mouseMove(oral.buttons[0], QPoint(12, 20))
    app.processEvents()
    assert _button_background(oral.buttons[0]) == LEVEL_COLORS[0].hover
    QTest.mouseClick(oral.buttons[0], Qt.MouseButton.LeftButton)
    app.processEvents()
    assert oral.buttons[0].isChecked() and oral.hasFocus()
    assert _button_background(oral.buttons[0]) == LEVEL_COLORS[0].selected, (
        "Hover must not put white selected text on the pale unselected background"
    )
    assert oral.buttons[0].property("rubricFocus") is True
    assert oral.buttons[0].font().bold()
    panel.oral_toggle.setFocus()
    app.processEvents()
    assert oral.buttons[0].property("rubricFocus") is False
    panel.set_written(False)
    panel.written_toggle.click()
    app.processEvents()
    written = panel.controls[0]
    panel.ensureWidgetVisible(written)
    app.processEvents()
    before = panel.levels()
    for index, button in enumerate(written.buttons):
        assert not button.isEnabled()
        expected = DISABLED_SELECTED if index == written.value() else DISABLED_TINT
        assert _button_background(button) == expected
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert panel.levels() == before
    panel.set_written(True)
    app.processEvents()
    assert _button_background(written.buttons[0]) == LEVEL_COLORS[1].tint
    assert _button_background(written.buttons[1]) == LEVEL_COLORS[2].selected
    assert _button_background(written.buttons[2]) == LEVEL_COLORS[3].tint
