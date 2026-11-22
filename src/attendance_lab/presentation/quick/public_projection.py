"""Public capability boundary: only a safe result and a safe lesson caption enter."""

from PySide6.QtCore import Property, QObject, QTimer, Signal

from attendance_lab.domain.models import PublicResult


class PublicProjection(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._context = ""
        self._open = False
        self._headline = "Регистрация закрыта"
        self._person = ""
        self._detail = "Ожидайте начала занятия"
        self._color = "#173f72"
        self._timeout = 3000
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.idle)

    context = Property(str, lambda self: self._context, notify=changed)
    headline = Property(str, lambda self: self._headline, notify=changed)
    person = Property(str, lambda self: self._person, notify=changed)
    detail = Property(str, lambda self: self._detail, notify=changed)
    color = Property(str, lambda self: self._color, notify=changed)

    def set_context(self, caption: str, registration_open: bool):
        if (caption, registration_open) != (self._context, self._open):
            self._context, self._open = caption, registration_open
            self.idle()

    def set_timeout(self, seconds: int):
        self._timeout = max(1, min(60, seconds)) * 1000
        if self.timer.isActive():
            self.timer.start(self._timeout)

    def idle(self):
        self.timer.stop()
        self._headline = "Приложите карту" if self._open else "Регистрация закрыта"
        self._person = ""
        self._detail = "Дождитесь подтверждения" if self._open else "Ожидайте начала занятия"
        self._color = "#173f72"
        self.changed.emit()

    def display(self, result: PublicResult):
        # Arbitrary result.message, errors and technical details never enter QML.
        if result.kind in ("idle", "closed"):
            if result.kind == "closed":
                self._open = False
            self.idle()
            return
        self._headline = {
            "success": "Отметка сохранена",
            "repeat": "Отметка уже существует",
            "error": "Не удалось сохранить отметку",
        }.get(result.kind, "Повторите попытку")
        self._color = {"success": "#166534", "repeat": "#854d0e", "error": "#991b1b"}.get(
            result.kind, "#173f72"
        )
        self._person = (result.full_name or "") if result.kind in ("success", "repeat") else ""
        if result.kind == "error":
            self._detail = "Обратитесь к преподавателю: запись не подтверждена"
        else:
            parts = ["Преподаватель сопоставит позже"] if not self._person else []
            if result.at is not None:
                parts.append(result.at.strftime("%H:%M:%S %Z"))
            self._detail = "\n".join(parts)
        self.changed.emit()
        self.timer.start(self._timeout)
