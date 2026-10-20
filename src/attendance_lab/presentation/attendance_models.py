"""Stable identity table models and an in-memory operator search index."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

IDENTITY = Qt.ItemDataRole.UserRole
PAIR = int(Qt.ItemDataRole.UserRole) + 1
SORT = int(Qt.ItemDataRole.UserRole) + 2


def normalized(value):
    return str(value).casefold().replace("ё", "е")


class StudentSearch:
    def __init__(self, students):
        self.students = list(students)
        self.entries = [
            (
                s,
                tuple(
                    normalized(
                        " ".join(
                            str(getattr(s, key, "") or "")
                            for key in (
                                "full_name",
                                "record_book_number",
                                "group_number",
                                "specialty",
                                "profile",
                            )
                        )
                    ).split()
                ),
            )
            for s in self.students
        ]
        # Names and catalog labels repeat across students. Score each distinct
        # token once per query, without consulting storage during typing.
        self.tokens = frozenset(token for _, tokens in self.entries for token in tokens)
        self.deletion_index = {}
        for token in self.tokens:
            if token.isdecimal() or len(token) > 40:
                continue
            for deletion in self.deletions(token):
                self.deletion_index.setdefault(deletion, set()).add(token)
        self.query_cache = {}

    @staticmethod
    def deletions(word):
        """Candidate lookup for at most two edits, built once for the roster."""
        found, level = {word}, {word}
        for _ in range(2):
            level = {text[:i] + text[i + 1 :] for text in level for i in range(len(text))}
            found.update(level)
        return found

    def word_scores(self, word):
        scores = {token: 1.0 for token in self.tokens if word in token}
        if len(word) < 3 or word.isdecimal() or len(word) > 40:
            return scores
        candidates = set()
        for deletion in self.deletions(word):
            candidates.update(self.deletion_index.get(deletion, ()))
        for token in candidates - scores.keys():
            score = self.token_score(word, token)
            if score:
                scores[token] = score
        return scores

    @staticmethod
    def token_score(word, token):
        if word in token:
            return 1.0
        if len(word) < 3:
            return 0.0
        limit = min(2, int(max(len(word), len(token)) * 0.32))
        if abs(len(word) - len(token)) > limit:
            return 0.0
        # Bounded Damerau-Levenshtein recognizes the adjacent transpositions
        # common when typing a name from speech. Length and row cutoffs avoid
        # the quadratic, allocation-heavy general-purpose sequence matcher.
        previous = list(range(len(token) + 1))
        before_previous = None
        for i, left in enumerate(word, 1):
            current = [i]
            for j, right in enumerate(token, 1):
                distance = min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left != right),
                )
                if (
                    before_previous is not None
                    and j > 1
                    and left == token[j - 2]
                    and word[i - 2] == right
                ):
                    distance = min(distance, before_previous[j - 2] + 1)
                current.append(distance)
            if min(current) > limit:
                return 0.0
            before_previous, previous = previous, current
        distance = previous[-1]
        return 1.0 - distance / max(len(word), len(token)) if distance <= limit else 0.0

    def find(self, query):
        query = " ".join(normalized(query).split())
        if query in self.query_cache:
            return list(self.query_cache[query])
        words = query.split()
        if not words:
            return sorted(self.students, key=lambda student: student.full_name)
        scores = {word: self.word_scores(word) for word in set(words)}
        ranked = []
        for student, tokens in self.entries:
            parts = [
                max((scores[word].get(token, 0) for token in tokens), default=0) for word in words
            ]
            score = sum(parts)
            if all(parts) and score / len(words) >= 0.68:
                ranked.append((score, student))
        result = [
            student for _, student in sorted(ranked, key=lambda item: (-item[0], item[1].full_name))
        ]
        if len(self.query_cache) >= 128:
            self.query_cache.pop(next(iter(self.query_cache)))
        self.query_cache[query] = tuple(result)
        return result


class StudentModel(QAbstractTableModel):
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
                else f"Пара #{self.pair_ids[section - len(self.headers)]}"
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
