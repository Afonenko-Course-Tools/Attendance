"""One command surface for mouse and private-window keyboard operations."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QListWidget, QVBoxLayout

from attendance_lab.presentation.common import guarded


class Commands:
    def __init__(self, window):
        self.window = window
        self.actions = []
        menu = window.menuBar().addMenu("Команды · F1")
        definitions = [
            ("Команды", "F1", self.show),
            ("Отложить карту / выйти из ручного поиска", "Escape", window.workspace.defer),
            ("Ручная отметка выбранного студента", "F2", window.workspace.manual_current),
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
        if widget is self.window.course_workspace:
            self.window.course_workspace.tabs.setCurrentWidget(self.window.preparation)
            self.window.preparation.tabs.setCurrentWidget(self.window.students)
            widget = self.window.students
        elif widget is self.window.course_journal:
            field = self.window.course_workspace.journal_search
            field.setFocus()
            field.selectAll()
            return
        elif widget is self.window.grading_page:
            field = getattr(widget, "search", None)
            if field is not None:
                field.setFocus()
                field.selectAll()
            return
        if hasattr(widget, "open_search"):
            widget.open_search()
        field = getattr(widget, "search", None)
        if field is not None:
            field.setFocus()
            field.selectAll()

    def focus_region(self, direction):
        current = self.window.tabs.currentWidget()
        regions = [self.window.course_workspace.courses, self.window.tabs.tabBar()]
        if current is self.window.workspace:
            regions += [
                current.roster_filter,
                current.blocks,
                current.table,
                current.course,
                current.search,
                current.matches,
                current.queue,
            ]
        elif current is self.window.course_workspace:
            regions += [current.tabs.tabBar()]
            child = current.tabs.currentWidget()
            if child is self.window.preparation:
                regions += [
                    child.tabs.tabBar(),
                    self.window.students.search,
                    self.window.students.students,
                ]
            else:
                regions += [current.topics, current.members, current.lessons]
        elif current is self.window.course_journal:
            regions += [
                self.window.course_workspace.journal_search,
                self.window.course_workspace.journal,
            ]
        elif current is self.window.grading_page:
            regions += [
                current.tabs.tabBar(),
                current.report_table,
                current.journal,
                current.changes,
            ]
        regions = [widget for widget in regions if widget.isVisible() and widget.isEnabled()]
        if not regions:
            return
        focused = QApplication.focusWidget()
        index = next(
            (
                i
                for i, widget in enumerate(regions)
                if widget is focused or widget.isAncestorOf(focused)
            ),
            -1,
        )
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
        elif self.window.tabs.currentWidget() is self.window.course_journal:
            self.window.course_workspace.mark_rectangle()
        elif self.window.tabs.currentWidget() is self.window.grading_page:
            grading = self.window.grading_page
            if grading.report_table.isVisible() and grading.open_report_action.isEnabled():
                guarded(self.window, grading.open_report)
