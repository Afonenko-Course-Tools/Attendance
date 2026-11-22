"""Shared operator zoom settings for the widget and Qt Quick interfaces."""

from PySide6.QtCore import Property, QObject, Signal, Slot


class InterfaceZoom(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent = 100

    @Property(int, notify=changed)
    def percent(self):
        return self._percent

    @Property(float, notify=changed)
    def factor(self):
        return self._percent / 100

    def scale_pixels(self, pixels: int) -> int:
        return round(pixels * self._percent / 100)

    @Slot()
    def zoomIn(self):
        self._set_percent(min(140, self._percent + 10))

    @Slot()
    def zoomOut(self):
        self._set_percent(max(80, self._percent - 10))

    @Slot()
    def reset(self):
        self._set_percent(100)

    def _set_percent(self, percent):
        if percent != self._percent:
            self._percent = percent
            self.changed.emit()
