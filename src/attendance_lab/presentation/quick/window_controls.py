"""Window placement and public timeout, without domain or storage capabilities."""

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

from attendance_lab.presentation.interface_zoom import InterfaceZoom


class WindowControls(QObject):
    changed = Signal()

    def __init__(self, public_window, projection, parent=None):
        super().__init__(parent)
        self.window, self.projection = public_window, projection
        self._interface_zoom = InterfaceZoom(self)
        self._timeout = 3
        self.window.visibleChanged.connect(self.changed)

    interfaceZoom = Property(QObject, lambda self: self._interface_zoom, constant=True)

    @Property(list, notify=changed)
    def screens(self):
        return [screen.name() for screen in QGuiApplication.screens()]

    @Property(bool, notify=changed)
    def publicVisible(self):
        return self.window.isVisible()

    @Property(int, notify=changed)
    def timeoutSeconds(self):
        return self._timeout

    @Slot()
    def togglePublic(self):
        self.window.setVisible(not self.window.isVisible())

    @Slot(int)
    def setScreen(self, index):
        screens = QGuiApplication.screens()
        if 0 <= index < len(screens):
            self.window.setScreen(screens[index])
            self.window.setPosition(screens[index].availableGeometry().topLeft())

    @Slot(bool)
    def setFullscreen(self, enabled):
        self.window.showFullScreen() if enabled else self.window.showNormal()

    @Slot(int)
    def setTimeout(self, seconds):
        self._timeout = max(1, min(60, seconds))
        self.projection.set_timeout(self._timeout)
        self.changed.emit()
