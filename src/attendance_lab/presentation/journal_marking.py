"""Mark journal topic cells using explicit identities and actual lesson instances."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox

from attendance_lab.application.journal import (
    DEFAULT_REASON,
    JOURNAL_REASONS,
    TOGGLE_MESSAGES,
    JournalMarkRequest,
    JournalOccurrenceChoice,
    JournalTopicTarget,
    confirm_journal_mark,
    prepare_journal_mark,
)
from attendance_lab.domain.redesign import EXCUSED_ABSENCE_REASON
from attendance_lab.presentation.common import guarded, plain_label
from attendance_lab.presentation.lesson_picker import LessonPicker, LessonPickerOption
from attendance_lab.presentation.preparation import FormDialog, combo


def _instance_label(occurrence: JournalOccurrenceChoice):
    start, end = occurrence.starts_at, occurrence.ends_at
    finish = end.strftime("%H:%M" if end.date() == start.date() else "%d.%m.%Y %H:%M")
    return f"{start:%d.%m.%Y %H:%M}–{finish} · {occurrence.roster_name}"


def mark_journal_cells(
    parent,
    service,
    course_id,
    selected_cells,
    changed,
    *,
    reason=DEFAULT_REASON,
    toggle=False,
    revoke_only=False,
    inform=None,
) -> bool:
    """Mark selected journal cells or explicitly revoke their manual evidence."""
    if course_id is None or not selected_cells:
        return False
    request = JournalMarkRequest(
        course_id, frozenset(selected_cells), reason, toggle, revoke_only=revoke_only
    )

    def execute():
        prepared = prepare_journal_mark(service, request)
        selected_reason = prepared.reason
        if not prepared.cells:
            if inform is not None and (revoke_only or prepared.mode == "card"):
                inform(TOGGLE_MESSAGES[prepared.mode])
            return False
        revoking = prepared.mode == "revoke"
        target_ids = {
            target.topic_id: target.occurrence_id for target in prepared.automatic_targets
        }
        if prepared.requires_choice:
            operation = "Отмена ручной отметки" if revoking else "Ручная отметка"
            dialog = FormDialog(f"{operation} — выберите занятие", parent)
            dialog.resize(650, 360)
            dialog.form.addRow(plain_label(prepared.course_name, wrap=True))
            if revoke_only:
                dialog.form.addRow(
                    plain_label("Выберите занятие для отмены отметок каждой темы.", wrap=True)
                )
            selectors = {}
            for topic in prepared.topics:
                dialog.form.addRow(
                    plain_label(
                        f"{topic.number}. {topic.title}\nСтуденты: {', '.join(topic.student_names)}",
                        wrap=True,
                    )
                )
                occurrences = topic.occurrences
                if len(occurrences) == 1:
                    dialog.form.addRow(
                        "Занятие",
                        plain_label(_instance_label(occurrences[0]), wrap=True),
                    )
                elif len(occurrences) <= 4:
                    selector = combo(
                        [("Выберите дату и группу…", None)]
                        + [(_instance_label(item), item.occurrence_id) for item in occurrences]
                    )
                    selector.setObjectName(f"TopicOccurrence:{topic.topic_id}")
                    selectors[topic.topic_id] = selector
                    dialog.form.addRow("Занятие", selector)
                else:
                    picker = LessonPicker(dialog)
                    picker.replace_choices(
                        LessonPickerOption(
                            item.occurrence_id,
                            item.starts_at.date(),
                            f"{item.starts_at:%H:%M}–{item.ends_at:%H:%M}",
                            topic.title,
                            item.roster_name,
                            label=_instance_label(item),
                        )
                        for item in occurrences
                    )
                    picker.setObjectName(f"TopicOccurrence:{topic.topic_id}")
                    selectors[topic.topic_id] = picker
                    dialog.form.addRow("Занятие", picker)
            reason_control = combo([(value, value) for value in JOURNAL_REASONS])
            reason_control.setCurrentIndex(JOURNAL_REASONS.index(selected_reason))
            reason_control.setObjectName("JournalMarkReason")
            if not revoking:
                dialog.form.addRow("Причина", reason_control)
            dialog.finish()
            save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
            assert save is not None

            def update_save(*_):
                save.setText(
                    "Отменить ручную отметку"
                    if revoking
                    else "Отметить отсутствие"
                    if reason_control.currentData() == EXCUSED_ABSENCE_REASON
                    else "Поставить посещение"
                )
                save.setEnabled(
                    all(control.currentData() is not None for control in selectors.values())
                )

            for control in selectors.values():
                control.currentIndexChanged.connect(update_save)
            reason_control.currentIndexChanged.connect(update_save)
            update_save()
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            target_ids.update({key: control.currentData() for key, control in selectors.items()})
            selected_reason = reason_control.currentText()
        result = confirm_journal_mark(
            service,
            prepared,
            tuple(JournalTopicTarget(key, target) for key, target in target_ids.items()),
            reason=selected_reason,
        )
        if not result.changed:
            if inform is not None and revoke_only:
                inform(TOGGLE_MESSAGES["card" if result.mode == "card" else "empty"])
            return False
        changed()
        if inform is not None and (toggle or revoke_only):
            inform(
                "Отсутствие по уважительной причине отмечено."
                if result.mode == "mark" and selected_reason == EXCUSED_ABSENCE_REASON
                else TOGGLE_MESSAGES[result.mode]
            )
        return True

    return bool(guarded(parent, execute))
