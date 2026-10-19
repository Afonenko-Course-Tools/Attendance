"""Public display: it accepts safe results, never a reader, service, or UID."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QScreen
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from attendance_lab.domain.models import PublicResult
from attendance_lab.presentation.common import plain_label


class PublicWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Регистрация присутствия")
        self.resize(920, 680)
        self._lesson_label = ""
        self._open = False
        self._timeout_ms = 3000
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.show_idle)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(56, 48, 56, 48)
        self.context = plain_label(wrap=True)
        self.context.setStyleSheet("color: #b5cde5; font-size: 23px;")
        self.context.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.context)
        layout.addStretch(2)
        self.headline = plain_label(wrap=True)
        self.headline.setStyleSheet("font-size: 46px; font-weight: 700; color: #ffffff;")
        self.headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.headline)
        self.person = plain_label(wrap=True)
        self.person.setStyleSheet("font-size: 34px; color: #d1fae5; padding: 20px;")
        self.person.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.person)
        self.detail = plain_label(wrap=True)
        self.detail.setStyleSheet("font-size: 22px; color: #b5cde5;")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.detail)
        layout.addStretch(3)
        self.setCentralWidget(central)
        self.setStyleSheet("QMainWindow, QWidget { background: #10263f; }")
        self.show_idle()

    def set_timeout_seconds(self, seconds: int) -> None:
        self._timeout_ms = max(1, min(seconds, 60)) * 1000
        if self.timer.isActive():
            self.timer.start(self._timeout_ms)

    def set_lesson(self, label: str, *, registration_open: bool) -> None:
        if label != self._lesson_label or registration_open != self._open:
            self.timer.stop()
            self._lesson_label = label
            self._open = registration_open
            self.show_idle()
        self.context.setText(label)

    def show_idle(self) -> None:
        self.person.clear()
        self._set_feedback_color("#ffffff")
        self.headline.setText("Приложите карту" if self._open else "Регистрация закрыта")
        self.detail.setText("Дождитесь подтверждения" if self._open else "Ожидайте начала занятия")

    def _set_feedback_color(self, color: str) -> None:
        self.headline.setStyleSheet(f"font-size: 46px; font-weight: 700; color: {color};")
        self.person.setStyleSheet(f"font-size: 34px; color: {color}; padding: 20px;")

    def display(self, result: PublicResult) -> None:
        self.timer.stop()
        self.person.clear()
        self.detail.clear()
        if result.kind in ("idle", "closed"):
            if result.kind == "closed":
                self._open = False
            self.show_idle()
            return
        # Fixed presentation messages deliberately ignore technical or arbitrary text.
        messages = {
            "success": "Отметка сохранена",
            "repeat": "Отметка уже существует",
            "error": "Не удалось сохранить отметку",
        }
        self.headline.setText(messages.get(result.kind, "Повторите попытку"))
        self._set_feedback_color(
            {"success": "#86efac", "repeat": "#fde047", "error": "#fca5a5"}.get(
                result.kind, "#ffffff"
            )
        )
        if result.kind == "error":
            self.detail.setText("Повторите попытку или обратитесь к преподавателю")
        else:
            self.person.setText(result.full_name or "")
            parts = []
            if not result.full_name:
                parts.append("Преподаватель сопоставит карту позднее")
            if result.at:
                parts.append(result.at.strftime("%H:%M:%S %Z"))
            self.detail.setText("\n".join(parts))
        self.timer.start(self._timeout_ms)

    def move_to_screen(self, screen: QScreen) -> None:
        fullscreen = self.isFullScreen()
        if fullscreen:
            self.showNormal()
        self.show()
        handle = self.windowHandle()
        if handle:
            handle.setScreen(screen)
        self.move(screen.availableGeometry().topLeft())
        if fullscreen:
            self.showFullScreen()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(event)
