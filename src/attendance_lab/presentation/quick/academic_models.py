"""Stable-ID Qt Quick journal table; no service or database capabilities."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Slot

from attendance_lab.domain.redesign import EXCUSED_ABSENCE_REASON


class JournalTableModel(QAbstractTableModel):
    DISPLAY = int(Qt.ItemDataRole.DisplayRole)
    STUDENT = int(Qt.ItemDataRole.UserRole) + 1
    TOPIC = STUDENT + 1
    PRESENT = STUDENT + 2
    MISSED = STUDENT + 3
    UNRESOLVED = STUDENT + 4
    TITLE = STUDENT + 5
    METADATA_COLUMNS = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.topics = []
        self._student_rows = {}
        self._topic_columns = {}
        self._source_rows = []
        self._view_sort = ""
        self._descending = False

    def _ordered(self, rows):
        if not self._view_sort:
            return list(rows)

        def key(row):
            value = row.get(self._view_sort)
            if self._view_sort in ("group_number", "subgroup", "missed_count"):
                try:
                    return (0, int(value), row["name"].casefold())
                except TypeError, ValueError:
                    return (1, 0, row["name"].casefold())
            return (0, str(value or "").casefold(), row["name"].casefold())

        return sorted(rows, key=key, reverse=self._descending)

    @Slot(str, bool)
    def set_view_sort(self, field, descending):
        if field not in (
            "",
            "name",
            "group_number",
            "subgroup",
            "specialty",
            "profile",
            "missed_count",
            "record_book_number",
        ):
            return
        if (field, descending) == (self._view_sort, self._descending):
            return
        self._view_sort, self._descending = field, descending
        self.set_snapshot({"rows": self._source_rows, "topics": self.topics})

    def set_snapshot(self, journal):
        self._source_rows = list(journal.get("rows", []))
        rows = self._ordered(self._source_rows)
        topics = list(journal.get("topics", []))
        same_shape = [row["id"] for row in self.rows] == [row["id"] for row in rows] and [
            topic["id"] for topic in self.topics
        ] == [topic["id"] for topic in topics]
        changed_rows = [
            index for index, row in enumerate(rows) if same_shape and self.rows[index] != row
        ]
        topics_changed = self.topics != topics
        if not same_shape:
            self.beginResetModel()
        self.rows, self.topics = rows, topics
        self._student_rows = {row["id"]: index for index, row in enumerate(self.rows)}
        self._topic_columns = {topic["id"]: index for index, topic in enumerate(self.topics)}
        if not same_shape:
            self.endResetModel()
        else:
            if topics_changed and self.topics:
                self.headerDataChanged.emit(
                    Qt.Orientation.Horizontal, self.METADATA_COLUMNS, self.columnCount() - 1
                )
                changed_rows = list(range(len(self.rows)))
            for row in changed_rows:
                self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))

    def rowCount(self, parent=QModelIndex()):  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: B008
        return 0 if parent.isValid() else self.METADATA_COLUMNS + len(self.topics) + 1

    def roleNames(self):
        return {
            self.DISPLAY: b"display",
            self.STUDENT: b"studentId",
            self.TOPIC: b"topicId",
            self.PRESENT: b"isPresent",
            self.MISSED: b"isMissed",
            self.UNRESOLVED: b"isUnresolved",
            self.TITLE: b"cellTitle",
        }

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        if section < self.METADATA_COLUMNS:
            return ("ФИО", "Группа", "Подгруппа", "Специальность", "Профиль")[section]
        index = section - self.METADATA_COLUMNS
        return str(self.topics[index]["number"]) if 0 <= index < len(self.topics) else "Пропуски"

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        column = index.column()
        if role == self.STUDENT:
            return row["id"]
        if column < self.METADATA_COLUMNS or column == self.columnCount() - 1:
            if role == self.DISPLAY:
                if column == self.columnCount() - 1:
                    return row["absence_text"]
                return str(
                    (
                        row["name"],
                        row.get("group_number") or "—",
                        row.get("subgroup") or "—",
                        row.get("specialty") or "—",
                        row.get("profile") or "—",
                    )[column]
                )
            if role == self.TITLE:
                return str(row.get("record_book_number", ""))
            if role == self.TOPIC:
                return -1
            return False
        offset = column - self.METADATA_COLUMNS
        if offset >= len(self.topics):
            return None
        topic = self.topics[offset]
        cell = next((cell for cell in row["cells"] if cell["topic_id"] == topic["id"]), {})
        if role == self.TOPIC:
            return topic["id"]
        if role == self.DISPLAY:
            return (
                "✓"
                if cell.get("present")
                else "У"
                if cell.get("excused")
                else "—"
                if cell.get("missed")
                else ""
            )
        if role in (self.PRESENT, self.MISSED, self.UNRESOLVED):
            return bool(
                cell.get(
                    {self.PRESENT: "present", self.MISSED: "missed", self.UNRESOLVED: "unresolved"}[
                        role
                    ]
                )
            )
        if role == self.TITLE:
            return (
                f"{topic['name']}\n{EXCUSED_ABSENCE_REASON}"
                if cell.get("excused")
                else topic["name"]
            )
        return None

    @Slot(str, int, int, int, result="QVariantMap")
    def neighbor(self, student_id, topic_id, row_delta, column_delta):
        if not self.rows or not self.topics:
            return {}
        row = max(0, min(len(self.rows) - 1, self._student_rows.get(student_id, 0) + row_delta))
        column = max(
            0, min(len(self.topics) - 1, self._topic_columns.get(topic_id, 0) + column_delta)
        )
        return {"student_id": self.rows[row]["id"], "topic_id": self.topics[column]["id"]}

    @Slot(str, int, str, int, result="QVariantList")
    def rectangle(self, first_student, first_topic, last_student, last_topic):
        if (
            first_student not in self._student_rows
            or last_student not in self._student_rows
            or first_topic not in self._topic_columns
            or last_topic not in self._topic_columns
        ):
            return []
        row_start, row_end = sorted(
            (self._student_rows[first_student], self._student_rows[last_student])
        )
        col_start, col_end = sorted(
            (self._topic_columns[first_topic], self._topic_columns[last_topic])
        )
        return [
            {"student_id": self.rows[row]["id"], "topic_id": self.topics[column]["id"]}
            for row in range(row_start, row_end + 1)
            for column in range(col_start, col_end + 1)
        ]
