"""External QML collection models; selection is stored by identity in the screen."""

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class RecordListModel(QAbstractListModel):
    RECORD = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def roleNames(self):
        return {self.RECORD: b"record"}

    def rowCount(self, parent=QModelIndex()):  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and 0 <= index.row() < len(self.rows) and role == self.RECORD:
            return self.rows[index.row()]
        return None

    @staticmethod
    def identity(row, index):
        return row.get("id", row.get("student_id", row.get("uid", row.get("key", index))))

    def set_rows(self, rows):
        rows = list(rows)
        old_ids = [self.identity(row, i) for i, row in enumerate(self.rows)]
        new_ids = [self.identity(row, i) for i, row in enumerate(rows)]
        if old_ids != new_ids:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()
        else:
            changed = [i for i, row in enumerate(rows) if self.rows[i] != row]
            self.rows = rows
            for i in changed:
                self.dataChanged.emit(self.index(i), self.index(i), [self.RECORD])
