"""Stable identity table models and an in-memory operator search index."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from attendance_lab.application.text_search import TextSearch
from attendance_lab.application.text_search import normalized as normalized

IDENTITY = Qt.ItemDataRole.UserRole
PAIR = int(Qt.ItemDataRole.UserRole) + 1
SORT = int(Qt.ItemDataRole.UserRole) + 2


class StudentSearch:
    def __init__(self, students):
        self.whole_block = False
        self.students = list(students)
        self.index = TextSearch(
            self.students,
            text=lambda student: " ".join(
                str(getattr(student, key, "") or "")
                for key in (
                    "full_name",
                    "record_book_number",
                    "group_number",
                    "specialty",
                    "profile",
                )
            ),
            order=lambda student: student.full_name,
        )

    def find(self, query):
        return self.index.find(query)


class StudentModel(QAbstractTableModel):
    whole_block: bool = False
    headers = ("ФИО", "Зачётная книжка", "Группа", "Курс", "Специальность", "Профиль", "Подгруппа")
    keys = (
        "full_name",
        "record_book_number",
        "group_number",
        "course",
        "specialty",
        "profile",
        "subgroup",
    )

    def __init__(self, students=(), parent=None):
        super().__init__(parent)
        self.students = list(students)
        self.pair_ids = ()
        self.attendance = {}

    def set_students(self, students, pair_ids=()):
        self.beginResetModel()
        self.students = list(students)
        self.pair_ids = tuple(pair_ids)
        self.attendance = {}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: B008
        return 0 if parent.isValid() else len(self.students)

    def columnCount(self, parent=QModelIndex()):  # noqa: B008
        return len(self.headers) + len(self.pair_ids)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return (
                self.headers[section]
                if section < len(self.headers)
                else (
                    "Посещение занятия"
                    if getattr(self, "whole_block", False)
                    else f"Пара #{self.pair_ids[section - len(self.headers)]}"
                )
            )
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        student = self.students[index.row()]
        if role == IDENTITY:
            return student.student_id
        if index.column() >= len(self.headers):
            pair = self.pair_ids[index.column() - len(self.headers)]
            if role == PAIR:
                return pair
            if role in (SORT, Qt.ItemDataRole.DisplayRole):
                return self.attendance.get((student.student_id, pair), "Нет отметки")
            return None
        value = getattr(student, self.keys[index.column()], "")
        if role == SORT:
            return value or (0 if index.column() in (2, 3) else "")
        if role == Qt.ItemDataRole.DisplayRole:
            return str(value) if value not in ("", None) else "—"
        return None

    def append_students(self, students):
        values = list(students)
        if values:
            self.beginInsertRows(
                QModelIndex(), len(self.students), len(self.students) + len(values) - 1
            )
            self.students.extend(values)
            self.endInsertRows()

    def update_attendance(self, values):
        self.attendance = values
        if self.students and self.pair_ids:
            self.dataChanged.emit(
                self.index(0, len(self.headers)),
                self.index(len(self.students) - 1, self.columnCount() - 1),
                [Qt.ItemDataRole.DisplayRole, SORT],
            )


class StudentProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.sort_fields = []
        self.book_column = 1
        self.setSortRole(SORT)
        self.setFilterKeyColumn(-1)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_sort_fields(self, fields):
        self.sort_fields = fields
        self.invalidate()
        self.sort(0)

    def lessThan(self, left, right):
        model = self.sourceModel()
        for column, descending in self.sort_fields:
            a = model.index(left.row(), column).data(SORT)
            b = model.index(right.row(), column).data(SORT)
            if a != b:
                return a > b if descending else a < b
        a, b = left.data(SORT), right.data(SORT)
        if a == b:
            model = self.sourceModel()

            def tie(index):
                return (
                    normalized(model.index(index.row(), 0).data()),
                    str(model.index(index.row(), self.book_column).data()),
                    str(index.data(IDENTITY)),
                )

            return tie(left) < tie(right)
        return a < b
