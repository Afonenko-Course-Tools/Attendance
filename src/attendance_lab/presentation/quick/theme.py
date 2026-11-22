"""The established light operator palette for the Qt Quick presentation."""

import os
from importlib.resources import files

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtQuickControls2 import QQuickStyle


def configure_style() -> None:
    """Choose the office-style controls before the first QML engine loads them."""
    # Fusion reads its own theme defaults instead of QGuiApplication.palette().
    # A packaged Qt configuration also covers detached windows and standalone pages.
    os.environ["QT_QUICK_CONTROLS_CONF"] = str(
        files("attendance_lab.presentation.quick").joinpath("qtquickcontrols2.conf")
    )
    if QQuickStyle.name() != "Fusion":
        QQuickStyle.setStyle("Fusion")


def apply_light_theme(app: QGuiApplication) -> None:
    """Use the same colors and font as the original operator windows."""
    configure_style()
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setFont(QFont("Arial", 12))
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#f3f6fa",
        QPalette.ColorRole.WindowText: "#17243c",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#f5f8fc",
        QPalette.ColorRole.Text: "#17243c",
        QPalette.ColorRole.PlaceholderText: "#62718a",
        QPalette.ColorRole.Button: "#ffffff",
        QPalette.ColorRole.ButtonText: "#234667",
        QPalette.ColorRole.BrightText: "#b42318",
        QPalette.ColorRole.Light: "#ffffff",
        QPalette.ColorRole.Midlight: "#f3f6fa",
        QPalette.ColorRole.Mid: "#cbd5e2",
        QPalette.ColorRole.Dark: "#62718a",
        QPalette.ColorRole.Shadow: "#17243c",
        QPalette.ColorRole.Highlight: "#1768b8",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: "#1455a0",
        QPalette.ColorRole.LinkVisited: "#6b3fa0",
        QPalette.ColorRole.ToolTipBase: "#fff9db",
        QPalette.ColorRole.ToolTipText: "#17243c",
        QPalette.ColorRole.Accent: "#1768b8",
    }
    for role, color in colors.items():
        palette.setColor(QPalette.ColorGroup.All, role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#7b8798"))
    app.setPalette(palette)
