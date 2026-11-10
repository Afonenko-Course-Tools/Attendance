"""Presentation of a known absence count and unresolved expectations."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem


def update_topic_cell(item, student_name, topic, *, present, missed, observed_only):
    """Keep a cell's visible mark and explanation in sync after evidence changes."""
    item.setText("✓" if present else "—" if missed else "")
    status = (
        "Посещение подтверждено"
        if present
        else "Пропуск"
        if missed
        else "Ожидаемый состав курса ещё не задан"
        if observed_only
        else "Нет проведённого занятия для этого состава"
    )
    item.setToolTip(
        f"{student_name}\nТема {topic.number}: {topic.title}\n{status}"
        "\nДвойной щелчок, Enter или Пробел — поставить посещение"
    )


class AbsenceCountItem(QTableWidgetItem):
    def __init__(self, student_id, missed_topics, unresolved_topics, *, expected_known=True):
        self.count = len(missed_topics)
        self.incomplete = bool(unresolved_topics) or not expected_known
        super().__init__(f"≥{self.count}" if self.count else "—")
        if not self.incomplete:
            self.setData(Qt.ItemDataRole.DisplayRole, self.count)
        self.setData(Qt.ItemDataRole.UserRole, student_id)
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(
            f"Пропусков по известному составу: {self.count}."
            + (
                f"\nУ {len(unresolved_topics)} тем не уточнён исторический состав; "
                "полное число пропусков пока неизвестно."
                if self.incomplete
                else "\nВсе прошедшие темы с известным составом учтены."
            )
        )
        if not expected_known:
            self.setToolTip("Ожидаемый состав курса ещё не задан; пропуски пока не рассчитываются.")

    def __lt__(self, other):
        if isinstance(other, AbsenceCountItem):
            return (self.count, self.incomplete) < (other.count, other.incomplete)
        return super().__lt__(other)
