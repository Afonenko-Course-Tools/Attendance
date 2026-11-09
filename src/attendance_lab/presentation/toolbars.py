"""Small office toolbars with accessible text and monochrome standard Qt icons."""

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QMenu, QStyle, QToolBar, QToolButton, QWidget


def office_toolbar(parent: QWidget | None = None) -> QToolBar:
    toolbar = QToolBar(parent)
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    toolbar.setIconSize(QSize(16, 16))
    toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    toolbar.setStyleSheet(
        "QToolBar { border: 0; spacing: 3px; padding: 0; } QToolButton { padding: 4px 6px; }"
    )
    return toolbar


def tool_action(
    toolbar: QToolBar,
    text: str,
    icon: QStyle.StandardPixmap,
    callback: Callable[[], object],
    tooltip: str = "",
) -> QAction:
    source = toolbar.style().standardIcon(icon).pixmap(16, 16).toImage()
    image = QImage(source.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    tint = toolbar.palette().color(QPalette.ColorRole.ButtonText)
    # White paper inside standard file icons becomes transparent; darker details
    # retain their original antialiasing instead of turning into solid squares.
    for y in range(source.height()):
        for x in range(source.width()):
            pixel = source.pixelColor(x, y)
            luminance = (pixel.red() * 299 + pixel.green() * 587 + pixel.blue() * 114) // 1000
            ink = QColor(tint)
            contrast = 0 if luminance >= 235 else min(255, (255 - luminance) * 3)
            ink.setAlpha(contrast * pixel.alpha() // 255)
            image.setPixelColor(x, y, ink)
    pixmap = QPixmap.fromImage(image)
    action = QAction(QIcon(pixmap), text, toolbar)
    action.setToolTip(tooltip or text)
    action.triggered.connect(lambda _checked=False: callback())
    toolbar.addAction(action)
    return action


def tool_menu(toolbar: QToolBar, text: str = "Ещё") -> QMenu:
    button = QToolButton(toolbar)
    button.setText(text)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(button)
    button.setMenu(menu)
    toolbar.addWidget(button)
    return menu
