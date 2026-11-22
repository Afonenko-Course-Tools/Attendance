"""Choose an existing programme topic or explicitly create a new numbered topic."""

from PySide6.QtWidgets import QFormLayout, QLineEdit, QVBoxLayout, QWidget

from attendance_lab.presentation.common import plain_label
from attendance_lab.presentation.preparation import FormDialog, combo, spin


class ProgrammeTopicChoice(QWidget):
    def __init__(self, api, course_id, parent=None, *, selected_topic_id=None):
        super().__init__(parent)
        self.api, self.course_id = api, course_id
        topics = api.list_topics(course_id)
        self.selector = combo(
            [(f"{topic.number}. {topic.title}", topic.id) for topic in topics]
            + [("Новая тема…", None)]
        )
        self.selector.setObjectName("ProgrammeTopicSelector")
        self.new_fields = QWidget()
        form = QFormLayout(self.new_fields)
        form.setContentsMargins(0, 0, 0, 0)
        self.number = spin(1, 1_000_000, max((topic.number for topic in topics), default=0) + 1)
        self.number.setObjectName("NewProgrammeTopicNumber")
        self.title = QLineEdit()
        self.title.setObjectName("NewProgrammeTopicTitle")
        self.title.setPlaceholderText("Название новой темы программы")
        form.addRow("Номер новой темы", self.number)
        form.addRow("Название", self.title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.selector)
        layout.addWidget(self.new_fields)
        self.selector.currentIndexChanged.connect(self.update_fields)
        if selected_topic_id is not None:
            self.selector.setCurrentIndex(self.selector.findData(selected_topic_id))
        self.update_fields()

    def update_fields(self, *_):
        self.new_fields.setVisible(self.selector.currentData() is None)

    def selection(self):
        """Return input values; the application owns validation and atomic saving."""
        selected = self.selector.currentData()
        if selected is None:
            return {"new_topic_number": self.number.value(), "new_topic_title": self.title.text()}
        return {"topic_id": selected}


def build_topic_dialog(parent, api, key, selected_topic_id, *, edit=True):
    topics = api.list_topics(key)
    selected = (
        next((topic for topic in topics if topic.id == selected_topic_id), None) if edit else None
    )
    if edit and selected is None:
        return
    d = FormDialog("Название темы программы" if edit else "Добавить тему программы", parent)
    number = spin(
        1,
        1_000_000,
        selected.number if selected else max((topic.number for topic in topics), default=0) + 1,
    )
    number.setEnabled(not edit)
    title = QLineEdit(selected.title if selected else "")
    title.setObjectName("ProgrammeTopicTitle")
    d.form.addRow("Номер темы", number)
    d.form.addRow("Название", title)
    if edit:
        d.form.addRow(
            plain_label(
                "Название изменится во всех занятиях этого курса с данным номером темы.",
                wrap=True,
            )
        )
    return (
        d,
        lambda: (api.save_topic if edit else api.add_topic)(key, number.value(), title.text()),
    )


def build_add_topics_dialog(parent, api, key):
    d = FormDialog("Добавить темы в программу", parent)
    first = spin(1, 1_000_000, max((topic.number for topic in api.list_topics(key)), default=0) + 1)
    count = spin(1, 1000, 1)
    d.form.addRow("Начальный номер", first)
    d.form.addRow("Сколько новых тем", count)
    d.form.addRow(
        plain_label(
            "Добавятся темы программы «Тема N». Названия можно изменить двойным щелчком. Занятия в расписании не создаются.",
            wrap=True,
        )
    )
    return d, lambda: api.add_topics(key, first.value(), count.value())


def build_lesson_topic_dialog(parent, service, key):
    lesson = service.courses.get_lesson(key)
    block = service.redesign.get_block(lesson.block_id)
    d = FormDialog("Тема выбранного занятия", parent)
    d.form.addRow(plain_label(service.lifecycle.describe(block.pair_ids[0]), wrap=True))
    choice = ProgrammeTopicChoice(
        service.courses, lesson.course_id, selected_topic_id=lesson.topic_id
    )
    d.form.addRow("Тема программы", choice)
    d.form.addRow(
        plain_label(
            "Изменится тема только этого занятия и зачёт посещения по теме. Исходные предъявления, ручные отметки и занятие сдачи доклада сохранятся.",
            wrap=True,
        )
    )

    def save():
        return service.courses.change_lesson_topic(key, **choice.selection())

    return d, save
