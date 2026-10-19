"""A complete light Qt theme for operator windows and their dialogs."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_light_theme(app: QApplication) -> None:
    """Set the theme before creating widgets; the public display styles itself."""
    # Widget dialogs follow our palette; platform file dialogs may follow OS dark mode.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("Fusion")
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
