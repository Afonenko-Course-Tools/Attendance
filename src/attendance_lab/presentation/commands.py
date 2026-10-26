"""One command surface for mouse and private-window keyboard operations."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QListWidget, QVBoxLayout


class Commands:
    def __init__(self, window):
        self.window = window
        self.actions = []
        menu = window.menuBar().addMenu("Команды · F1")
        definitions = [
            ("Команды", "F1", self.show),
            ("Отложить карту / выйти из ручного поиска", "Escape", window.workspace.defer),
            ("Ручная отметка активного сеанса", "F2", window.workspace.start_manual),
            ("Поиск на экране", "F3", self.search),
            ("Исправить последнюю карту", "F4", window.workspace.correct_last_card),
            ("Следующая область", "F6", lambda: self.focus_region(1)),
            ("Предыдущая область", "Shift+F6", lambda: self.focus_region(-1)),
            ("Открыть регистрацию блока", "F9", window.workspace.open_selected),
            ("Закрыть регистрацию", "Shift+F9", window.workspace.close_active),
            ("Подтвердить выбранное", "Ctrl+Return", self.confirm),
        ]
        for text, key, callback in definitions:
            action = QAction(f"{text} · {key}", window)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(lambda _=False, fn=callback: self.invoke(fn))
            window.addAction(action)
            menu.addAction(action)
            self.actions.append(action)

    def invoke(self, callback):
        if QApplication.activeModalWidget() is None and self.window.isActiveWindow():
            callback()

    def show(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Команды преподавателя")
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        for action in self.actions:
            listing.addItem(action.text())
        layout.addWidget(listing)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Выполнить")
        buttons.accepted.connect(dialog.accept)
        listing.itemActivated.connect(lambda *_: dialog.accept())
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(540, 380)
        if dialog.exec() == QDialog.DialogCode.Accepted and listing.currentRow() >= 0:
            action = self.actions[listing.currentRow()]
            if action is not self.actions[0]:
                QTimer.singleShot(0, action.trigger)

    def search(self):
        widget = self.window.tabs.currentWidget()
        if widget is self.window.preparation:
            widget = self.window.students
            self.window.preparation.tabs.setCurrentIndex(1)
        field = getattr(widget, "search", None)
        if field:
            field.setFocus()
            field.selectAll()

    def focus_region(self, direction):
        current = self.window.tabs.currentWidget()
        regions = [self.window.tabs]
        if current is self.window.workspace:
            regions += [
                current.blocks,
                current.table,
                current.course,
                current.search,
                current.matches,
                current.queue,
            ]
        elif current is self.window.history:
            regions += [current.discipline, current.search, current.matrix]
        elif current is self.window.preparation:
            regions += (
                [current.tabs.tabBar(), self.window.schedule.discipline]
                if current.tabs.currentIndex() == 0
                else [
                    current.tabs.tabBar(),
                    self.window.students.search,
                    self.window.students.students,
                ]
            )
        else:
            regions += [self.window.readers, self.window.screens]
        focused = QApplication.focusWidget()
        if focused is self.window.tabs.tabBar():
            focused = self.window.tabs
        index = regions.index(focused) if focused in regions else -1
        regions[(index + direction) % len(regions)].setFocus()

    def confirm(self):
        if self.window.tabs.currentWidget() is self.window.workspace:
            focus = QApplication.focusWidget()
            if focus is self.window.workspace.table or self.window.workspace.table.isAncestorOf(
                focus
            ):
                self.window.workspace.manual_block()
            else:
                self.window.workspace.confirm_match()
        elif self.window.tabs.currentWidget() is self.window.history:
            self.window.history.manual()
