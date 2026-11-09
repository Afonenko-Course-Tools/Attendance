"""Mark journal topic cells using explicit identities and actual lesson instances."""

from zoneinfo import ZoneInfo

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit

from attendance_lab.domain.models import AttendanceError
from attendance_lab.presentation.common import guarded, plain_label
from attendance_lab.presentation.preparation import FormDialog, combo

DEFAULT_REASON = "Ручная отметка преподавателя в журнале"


def _instance_label(service, occurrence, rosters):
    block = service.redesign.get_block(occurrence.block_id)
    pairs = [service.get_lesson(pair_id) for pair_id in block.pair_ids]
    zone = ZoneInfo(block.timezone)
    start = min(pair.starts_at for pair in pairs).astimezone(zone)
    end = max(pair.ends_at for pair in pairs).astimezone(zone)
    finish = end.strftime("%H:%M" if end.date() == start.date() else "%d.%m.%Y %H:%M")
    return f"{start:%d.%m.%Y %H:%M}–{finish} · {rosters[block.roster_id].name}"


def mark_journal_cells(parent, service, course_id, selected_cells, changed) -> bool:
    """Save missing attendance only; existing attendance is never toggled or revoked."""
    if course_id is None or not selected_cells:
        return False
    requested = frozenset(selected_cells)

    def execute():
        api = service.courses
        course = api.get_course(course_id)
        topics = {topic.id: topic for topic in api.list_topics(course.id)}
        if any(topic_id not in topics for _, topic_id in requested):
            raise AttendanceError("Выбранная ячейка не относится к этому курсу.")
        names = {student.id: student.full_name for student in service.list_students()}
        if any(student_id not in names for student_id, _ in requested):
            raise AttendanceError("Выбранный студент больше недоступен. Обновите журнал.")
        selected = requested.difference(api.attendance_projection(course.id))
        if not selected:
            return False
        topic_ids = {topic_id for _, topic_id in selected}
        lessons = api.list_lessons(course.id)
        candidates = {
            topic_id: [lesson for lesson in lessons if lesson.topic_id == topic_id]
            for topic_id in topic_ids
        }
        for topic_id, occurrences in candidates.items():
            if not occurrences:
                raise AttendanceError(
                    f"Для темы {topics[topic_id].number} «{topics[topic_id].title}» "
                    "ещё нет занятия в расписании. Сначала запланируйте занятие."
                )
        target_ids = {
            topic_id: occurrences[0].id
            for topic_id, occurrences in candidates.items()
            if len(occurrences) == 1
        }
        reason = DEFAULT_REASON
        if any(len(occurrences) > 1 for occurrences in candidates.values()):
            dialog = FormDialog("Поставить посещение — выберите занятие", parent)
            dialog.resize(650, 360)
            dialog.form.addRow(plain_label(course.name, wrap=True))
            rosters = {roster.id: roster for roster in api.list_rosters(course.id)}
            selectors = {}
            for topic_id in sorted(topic_ids, key=lambda key: topics[key].number):
                topic = topics[topic_id]
                people = sorted(names[student] for student, key in selected if key == topic_id)
                dialog.form.addRow(
                    plain_label(
                        f"{topic.number}. {topic.title}\nСтуденты: {', '.join(people)}", wrap=True
                    )
                )
                occurrences = candidates[topic_id]
                if len(occurrences) == 1:
                    dialog.form.addRow(
                        "Занятие",
                        plain_label(_instance_label(service, occurrences[0], rosters), wrap=True),
                    )
                else:
                    selector = combo(
                        [("Выберите дату и группу…", None)]
                        + [
                            (_instance_label(service, item, rosters), item.id)
                            for item in occurrences
                        ]
                    )
                    selector.setObjectName(f"TopicOccurrence:{topic_id}")
                    selectors[topic_id] = selector
                    dialog.form.addRow("Занятие", selector)
            reason_control = QLineEdit(DEFAULT_REASON)
            reason_control.setObjectName("JournalMarkReason")
            dialog.form.addRow("Причина", reason_control)
            dialog.finish()
            save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
            assert save is not None
            save.setText("Поставить посещение")

            def update_save(*_):
                save.setEnabled(
                    bool(reason_control.text().strip())
                    and all(control.currentData() is not None for control in selectors.values())
                )

            for control in selectors.values():
                control.currentIndexChanged.connect(update_save)
            reason_control.textChanged.connect(update_save)
            update_save()
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            target_ids.update({key: control.currentData() for key, control in selectors.items()})
            if any(target is None for target in target_ids.values()):
                raise AttendanceError("Явно выберите занятие для каждой отмечаемой темы.")
            reason = reason_control.text().strip()
        # A reader event can confirm a selected topic while the chooser is open.
        remaining = selected.difference(api.attendance_projection(course.id))
        if not remaining:
            return False
        api.mark_topics(course.id, remaining, target_ids, reason)
        changed()
        return True

    return bool(guarded(parent, execute))
