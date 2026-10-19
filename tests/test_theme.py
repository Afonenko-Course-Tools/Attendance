"""The private interface remains readable when the desktop uses dark colors."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.presentation.common import STYLE
from attendance_lab.presentation.public import PublicWindow
from attendance_lab.presentation.theme import apply_light_theme


@pytest.fixture
def light_app():
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    previous_palette = app.palette()
    previous_scheme = app.styleHints().colorScheme()
    previous_style = app.style().objectName()
    previous_dialogs = app.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    dark_palette = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        dark_palette.setColor(role, QColor("#121212"))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText):
        dark_palette.setColor(role, QColor("#eeeeee"))
    app.setPalette(dark_palette)
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    apply_light_theme(app)
    yield app
    app.setStyle(previous_style)
    app.styleHints().setColorScheme(previous_scheme)
    app.setPalette(previous_palette)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, previous_dialogs)


def test_private_inputs_tables_and_popups_override_dark_desktop(light_app):
    window = QMainWindow()
    window.setStyleSheet(STYLE)
    central = QWidget()
    layout = QVBoxLayout(central)
    field = QLineEdit("Читаемый текст")
    combo = QComboBox()
    combo.addItems(["Дисциплина", "Другая дисциплина"])
    calendar = QDateTimeEdit()
    calendar.setCalendarPopup(True)
    table = QTableWidget(1, 1)
    for widget in (field, combo, calendar, table):
        layout.addWidget(widget)
    window.setCentralWidget(central)
    window.show()
    light_app.processEvents()
    # The offscreen Qt platform reports Unknown even after an explicit request.
    assert light_app.styleHints().colorScheme() != Qt.ColorScheme.Dark
    assert light_app.style().objectName().lower() == "fusion"
    for widget in (field, table, combo.view(), calendar.calendarWidget()):
        widget.ensurePolished()
        assert widget.palette().color(QPalette.ColorRole.Base).name() == "#ffffff"
        assert widget.palette().color(QPalette.ColorRole.Text).name() == "#17243c"
    assert table.palette().color(QPalette.ColorRole.AlternateBase).name() == "#f5f8fc"
    window.close()


def test_private_dialogs_are_light_and_public_window_keeps_its_background(light_app):
    message = QMessageBox()
    file_dialog = QFileDialog()
    public = PublicWindow()
    public.show()
    light_app.processEvents()
    assert light_app.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    for widget in (message, file_dialog):
        widget.ensurePolished()
        assert widget.palette().color(QPalette.ColorRole.Window).name() == "#f3f6fa"
        assert widget.palette().color(QPalette.ColorRole.WindowText).name() == "#17243c"
    public_content = public.centralWidget()
    assert public_content is not None
    assert public_content.palette().color(QPalette.ColorRole.Window).name() == "#10263f"
    assert public.context.palette().color(QPalette.ColorRole.WindowText).name() == "#b5cde5"
    public.close()
    message.close()
    file_dialog.close()
