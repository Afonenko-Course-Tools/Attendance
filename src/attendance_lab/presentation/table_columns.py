"""Consistent column sizing and visibility in the operator tables."""

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication, QHeaderView, QMenu


class TableColumns(QObject):
    def __init__(
        self,
        table,
        name,
        menu=None,
        *,
        keys: Sequence[str] | Callable[[], Sequence[str]] = (),
        default_hidden=(),
        available=None,
    ):
        super().__init__(table)
        self.table = table
        self.header = table.horizontalHeader()
        self.settings = QSettings() if QApplication.organizationName() else None
        self.key = f"tables/{name}"
        self.keys = keys
        self.default_hidden = frozenset(default_hidden)
        self.available = available
        saved_widths = self.settings.value(f"{self.key}/widths", {}) if self.settings else None
        saved_hidden = self.settings.value(f"{self.key}/hidden", []) if self.settings else None
        saved_shown = self.settings.value(f"{self.key}/shown", []) if self.settings else None
        self.widths = saved_widths if isinstance(saved_widths, dict) else {}
        self.hidden = set(saved_hidden) if isinstance(saved_hidden, list) else set()
        self.shown = set(saved_shown) if isinstance(saved_shown, list) else set()
        self.viewport = table.viewport()
        self.header.setStretchLastSection(False)
        self.header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.header.sectionResized.connect(self.remember_width)
        self.header.sectionHandleDoubleClicked.connect(self.fit_one)
        self.header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.header.customContextMenuRequested.connect(self.show_menu)
        if menu is not None:
            menu.aboutToShow.connect(lambda: self.populate_menu(menu))
        model = table.model()
        for signal in (
            model.modelReset,
            model.rowsInserted,
            model.rowsRemoved,
            model.headerDataChanged,
        ):
            signal.connect(self.schedule_fit)
        table.installEventFilter(self)
        self.viewport.installEventFilter(self)
        self.pending = False
        self.applying = False
        self.schedule_fit()

    def eventFilter(self, watched, event):
        if watched is self.table and event.type() in (
            QEvent.Type.StyleChange,
            QEvent.Type.FontChange,
        ):
            self.schedule_fit()
        if watched is self.viewport and event.type() == QEvent.Type.Resize:
            self.schedule_fit()
        return False

    def labels(self):
        model = self.table.model()
        return [
            str(model.headerData(i, Qt.Orientation.Horizontal) or "")
            for i in range(self.header.count())
        ]

    def fields(self):
        labels = self.labels()
        keys = self.keys() if callable(self.keys) else self.keys
        return [
            (keys[column] if column < len(keys) else label, label)
            for column, label in enumerate(labels)
        ]

    def schedule_fit(self, *_):
        if not self.pending:
            self.pending = True
            QTimer.singleShot(0, self, self.fit)

    def scale(self):
        return self.table.fontMetrics().height() / 19

    def remember_width(self, column, old, size):
        if (
            not self.applying
            and size > 0
            and QApplication.mouseButtons() & Qt.MouseButton.LeftButton
        ):
            fields = self.fields()
            if column < len(fields) and fields[column][1]:
                key, label = fields[column]
                self.widths.pop(label, None)
                self.widths[key] = round(size / self.scale())
                self.save()

    def preferred_width(self, column, label, *, full=False):
        table = self.table
        metrics = table.fontMetrics()
        model = table.model()
        widths = sorted(
            max(
                (
                    metrics.horizontalAdvance(line)
                    for line in str(model.index(row, column).data() or "").splitlines()
                ),
                default=0,
            )
            for row in range(model.rowCount())
            if not table.isRowHidden(row)
        )
        sample = len(widths)
        content = (
            widths[-1 if full or sample < 20 else (sample * 95 + 99) // 100 - 1] if widths else 0
        )
        scale = self.scale()
        heading = max(
            (self.header.fontMetrics().horizontalAdvance(line) for line in label.splitlines()),
            default=0,
        ) + round(30 * scale)
        maximum = 960 if full else 440
        return max(heading, min(round(maximum * scale), content + round(26 * scale)))

    def fit(self):
        self.pending = False
        columns = {}
        flexible = []
        for column, (key, label) in enumerate(self.fields()):
            if label and not self.header.isSectionHidden(column):
                width = self.widths.get(key, self.widths.get(label))
                columns[column] = (
                    round(int(width) * self.scale())
                    if width
                    else self.preferred_width(column, label)
                )
                if width is None and label in (
                    "Состав",
                    "Тема",
                    "Тема доклада",
                    "Занятие сдачи",
                ):
                    flexible.append(column)
        free = max(0, self.table.viewport().width() - sum(columns.values()))
        for column in flexible:
            extra = min(free // len(flexible), max(0, round(420 * self.scale()) - columns[column]))
            columns[column] += extra
        self.applying = True
        try:
            for column, size in columns.items():
                self.header.resizeSection(column, size)
        finally:
            self.applying = False

    def restore_visibility(self):
        for column, (key, label) in enumerate(self.fields()):
            unavailable = self.available is not None and not self.available(key)
            hidden = (
                unavailable
                or column > 0
                and (
                    key in self.hidden
                    or label in self.hidden
                    or (key in self.default_hidden and key not in self.shown)
                )
            )
            self.header.setSectionHidden(column, hidden)
        self.schedule_fit()

    def fit_one(self, column):
        fields = self.fields()
        if column >= len(fields):
            return
        key, label = fields[column]
        self.widths.pop(label, None)
        width = self.preferred_width(column, label, full=True)
        self.widths[key] = round(width / self.scale())
        self.fit()
        self.save()

    def reset_widths(self):
        self.widths.clear()
        self.fit()
        self.save()

    def set_visible(self, column, key, label, visible):
        self.header.setSectionHidden(column, not visible)
        self.hidden.discard(label)
        self.hidden.discard(key)
        if visible:
            self.shown.add(key)
        else:
            self.hidden.add(key)
            self.shown.discard(key)
        self.schedule_fit()
        self.save()

    def populate_menu(self, menu):
        menu.clear()
        menu.addAction("Сбросить ширину колонок", self.reset_widths)
        menu.addSeparator()
        for column, (key, label) in enumerate(self.fields()):
            if column == 0 or not label or (self.available is not None and not self.available(key)):
                continue
            action = menu.addAction(" · ".join(label.splitlines()))
            action.setCheckable(True)
            action.setChecked(not self.header.isSectionHidden(column))
            action.toggled.connect(
                lambda checked, index=column, field=key, title=label: self.set_visible(
                    index, field, title, checked
                )
            )

    def show_menu(self, position):
        menu = QMenu(self.table)
        self.populate_menu(menu)
        menu.exec(self.header.mapToGlobal(position))
        menu.deleteLater()

    def save(self):
        if self.settings:
            self.settings.setValue(f"{self.key}/widths", self.widths)
            self.settings.setValue(f"{self.key}/hidden", sorted(self.hidden))
            self.settings.setValue(f"{self.key}/shown", sorted(self.shown))
