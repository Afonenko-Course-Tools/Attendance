"""Local editing state, independent of Qt, transport and control-state guards."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, TypedDict


class SelectedCell(TypedDict):
    student_id: str
    topic_id: int


class CourseEditingState:
    """Keep drafts by course and selections by stable identity, in visible order."""

    def __init__(self, course_id: int | None = None):
        self._course_id = course_id
        self._drafts_by_course: dict[int | None, dict[str, dict[str, Any]]] = {}
        self._selected_cells: list[SelectedCell] = []
        self._selected_students: list[str] = []
        self._cell_anchor: SelectedCell | None = None
        self._student_anchor: str | None = None

    def switch_course(self, course_id: int | None) -> None:
        if course_id != self._course_id:
            self._course_id = course_id
            self._selected_cells = []
            self._cell_anchor = None

    @property
    def drafts(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._drafts_by_course.get(self._course_id, {}))

    def set_draft(self, scope: str, key: str, value: Any) -> None:
        current = self._drafts_by_course.setdefault(self._course_id, {})
        current.setdefault(scope, {})[key] = deepcopy(value)

    @property
    def selected_cells(self) -> list[SelectedCell]:
        return deepcopy(self._selected_cells)

    @property
    def selected_students(self) -> list[str]:
        return list(self._selected_students)

    def select_cell(
        self,
        student_id: str,
        topic_id: int,
        student_ids: Sequence[str],
        topic_ids: Sequence[int],
        extend: bool = False,
    ) -> None:
        if student_id not in student_ids or topic_id not in topic_ids:
            return
        target: SelectedCell = {"student_id": student_id, "topic_id": topic_id}
        anchor = self._cell_anchor
        if (
            extend
            and anchor is not None
            and anchor["student_id"] in student_ids
            and anchor["topic_id"] in topic_ids
        ):
            first_row, last_row = sorted(
                (student_ids.index(anchor["student_id"]), student_ids.index(student_id))
            )
            first_column, last_column = sorted(
                (topic_ids.index(anchor["topic_id"]), topic_ids.index(topic_id))
            )
            self._selected_cells = [
                {"student_id": sid, "topic_id": tid}
                for sid in student_ids[first_row : last_row + 1]
                for tid in topic_ids[first_column : last_column + 1]
            ]
        else:
            self._selected_cells = [target]
            self._cell_anchor = target

    def select_student(
        self, student_id: str, visible_ids: Sequence[str], extend: bool = False
    ) -> None:
        if student_id not in visible_ids:
            return
        if extend and self._student_anchor in visible_ids:
            first, last = sorted(
                (visible_ids.index(self._student_anchor), visible_ids.index(student_id))
            )
            self._selected_students = list(visible_ids[first : last + 1])
        else:
            self._selected_students = [student_id]
            self._student_anchor = student_id

    def reconcile(
        self,
        student_ids: Sequence[str],
        cell_student_ids: Sequence[str],
        topic_ids: Sequence[int],
    ) -> None:
        """Discard hidden identities and anchors after filtering or a snapshot refresh."""
        visible_students = set(student_ids)
        visible_rows, visible_topics = set(cell_student_ids), set(topic_ids)
        self._selected_students = [
            sid for sid in self._selected_students if sid in visible_students
        ]
        if self._student_anchor not in visible_students:
            self._student_anchor = None
        self._selected_cells = [
            cell
            for cell in self._selected_cells
            if cell["student_id"] in visible_rows and cell["topic_id"] in visible_topics
        ]
        if self._cell_anchor is not None and (
            self._cell_anchor["student_id"] not in visible_rows
            or self._cell_anchor["topic_id"] not in visible_topics
        ):
            self._cell_anchor = None


class CardMatchDraft:
    """Capture the binding context once; edits never silently refresh that context."""

    def __init__(self):
        self._fields: dict[str, Any] = {}

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._fields)

    def get(self, key: str, default: Any = None) -> Any:
        return deepcopy(self._fields.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._fields[key] = deepcopy(value)

    def pick(self, card: Mapping[str, Any], correction: bool = False) -> None:
        if card["uid"] != self._fields.get("uid"):
            self._fields = {
                "uid": card["uid"],
                "expected_student_id": card.get("student_id"),
                "owner_name": card.get("full_name") or "",
                "binding_revision": card.get("binding_revision", 0),
                "correction": correction,
                "student_id": "",
                "query": "",
            }
        else:
            self._fields["correction"] = correction

    def clear(self) -> None:
        self._fields = {}
