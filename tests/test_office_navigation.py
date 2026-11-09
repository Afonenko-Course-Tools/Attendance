"""Office navigation and stream scheduling preserve fixed historical evidence."""

from datetime import UTC, date, datetime, timedelta

from PySide6.QtWidgets import QAbstractItemView, QApplication
from test_course_workflow import course  # noqa: F401

from attendance_lab.application.controller import AttendanceController
from attendance_lab.infrastructure.csv_io import CsvFiles
from attendance_lab.infrastructure.readers import DemoReader
from attendance_lab.presentation.admin import MainWindow
from attendance_lab.presentation.common import fill_table, selected_key, table


def test_clear_selection_does_not_leave_an_action_target():
    app = QApplication.instance() or QApplication([])
    listing = table(["Название"])
    fill_table(listing, [("Первая",), ("Вторая",)], [17, 29])
    listing.selectRow(1)
    assert selected_key(listing) == 29
    listing.clearSelection()
    assert listing.currentRow() == 1
    assert selected_key(listing) is None
    listing.selectRow(1)
    fill_table(listing, [("Новый объект",)], [41])
    assert selected_key(listing) is None
    listing.close()
    app.processEvents()


def test_cell_refresh_preserves_exact_selected_targets_without_selecting_other_topics():
    app = QApplication.instance() or QApplication([])
    listing = table(["Студент", "Тема 1", "Тема 2"])
    listing.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    fill_table(listing, [("Первый", "", ""), ("Второй", "", "")], [17, 29])
    listing.setCurrentCell(0, 1)
    fill_table(listing, [("Второй", "", ""), ("Первый", "✓", "")], [29, 17])
    assert [(i.row(), i.column()) for i in listing.selectedIndexes()] == [(1, 1)]
    assert listing.currentRow() == 1 and listing.currentColumn() == 1
    listing.close()
    app.processEvents()


def test_primary_navigation_keeps_grades_visible_and_technical_windows_separate(course, tmp_path):  # noqa: F811
    app = QApplication.instance() or QApplication([])
    service, selected, roster = course
    occurrence = service.courses.schedule(selected.id, roster.id, 1, date.today(), 1)
    window = MainWindow(
        service,
        AttendanceController(service, DemoReader),
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "office.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    window.show()
    app.processEvents()
    try:
        assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
            "Занятие",
            "Посещаемость",
            "Оценки",
            "Курс",
        ]
        assert window.tabs.indexOf(window.grading_page) >= 0
        assert window.tabs.isTabVisible(window.tabs.indexOf(window.grading_page))
        assert not service.grading.works(selected.id)
        assert not selected.reports_enabled
        assert not window.active_banner.isVisible()
        assert window.logs_window.isWindow() and not window.logs_window.isVisible()
        assert window.history.isWindow() and not window.history.isVisible()
        assert not hasattr(window, "embedded_log")
        window.workspace.select_lesson(service.redesign.get_block(occurrence.block_id).pair_ids[0])
        window._refresh_context()
        assert window.events_action.isEnabled()
        window.tabs.setCurrentWidget(window.course_journal)
        window.commands.search()
        assert window.course_workspace.journal_search.hasFocus()
    finally:
        window.close()
        app.processEvents()


def test_lecture_stream_uses_expected_members_only_and_keeps_historical_rosters(course):  # noqa: F811
    service, initial, _ = course
    group = service.redesign.list_groups()[0]
    lecture = service.courses.save_course(
        initial.discipline_id,
        initial.academic_year_id,
        1,
        2,
        "lecture",
        1,
        "Лекции",
    )
    students = [s.student_id for s in service.redesign.list_student_contexts()]
    first = service.courses.add_groups(lecture.id, {group.id}, "Прежний состав A", {students[0]})
    second = service.courses.add_groups(lecture.id, {group.id}, "Прежний состав Б", {students[1]})
    for number in range(1, 4):
        service.courses.save_topic(lecture.id, number, f"Тема {number}")
    occurrences = [
        service.courses.schedule(lecture.id, r.id, n, date.today() - timedelta(days=3 - n), 1)
        for n, r in enumerate((first, second), 1)
    ]
    headers = [
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    ]
    service.redesign.apply_import(
        service.redesign.preview_import(
            headers,
            [["3", "Вымышленный Третий", "Тест", "Профиль", "2", "1", "0003"]],
            2026,
        )
    )
    outsider = next(
        s.student_id
        for s in service.redesign.list_student_contexts()
        if s.student_id not in students
    )
    first_pair = service.redesign.get_block(occurrences[0].block_id).pair_ids[0]
    service.redesign.create_manual_batch(
        {(outsider, first_pair)}, "Синтетическое посещение вне состава"
    )
    historical = {
        lesson.block_id: (
            service.redesign.get_block(lesson.block_id),
            tuple(
                service.redesign.get_pair_contexts(
                    service.redesign.get_block(lesson.block_id).pair_ids[0]
                )
            ),
        )
        for lesson in occurrences
    }
    stream = service.courses.lecture_roster(lecture.id)
    assert set(stream.student_ids) == set(students)
    assert outsider not in stream.student_ids
    assert service.courses.lecture_roster(lecture.id).id == stream.id
    assert len(service.courses.list_rosters(lecture.id)) == 3
    for block_id, (block, contexts) in historical.items():
        assert service.redesign.get_block(block_id) == block
        assert tuple(service.redesign.get_pair_contexts(block.pair_ids[0])) == contexts
    new = service.courses.schedule(lecture.id, stream.id, 3, date.today() + timedelta(days=7), 1)
    expected = service.redesign.get_pair_contexts(
        service.redesign.get_block(new.block_id).pair_ids[0]
    )
    assert {s.student_id for s in expected} == set(students)


def test_grading_keyboard_and_actions_follow_visible_selection(course, tmp_path, monkeypatch):  # noqa: F811
    app = QApplication.instance() or QApplication([])
    service, selected, _ = course
    window = MainWindow(
        service,
        AttendanceController(service, DemoReader),
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "office-selection.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    window.show()
    try:
        pane = window.grading_page
        window.tabs.setCurrentWidget(pane)
        pane.enable_reports()
        service.grading.add_work(selected.id, "Контрольная", 20)
        pane.refresh()
        pane.report_table.selectRow(0)
        assert pane.open_report_action.isEnabled()
        pane.report_table.clearSelection()
        assert not pane.open_report_action.isEnabled()
        pane.journal.setCurrentCell(0, 1)
        assert pane.paste_action.isEnabled()
        pane.journal.clearSelection()
        assert not pane.paste_action.isEnabled()
        app.clipboard().setText("10")
        pane.paste_scores()
        assert service.grading.scores(selected.id) == {}
        pane.report_table.selectRow(0)
        calls = []
        monkeypatch.setattr(pane, "open_report", lambda: calls.append("report"))
        pane.tabs.setCurrentIndex(1)
        app.processEvents()
        window.commands.confirm()
        assert calls == []
        pane.tabs.setCurrentIndex(0)
        app.processEvents()
        window.commands.confirm()
        assert calls == ["report"]
        window.course_workspace.courses.setFocus()
        for _ in range(8):
            window.commands.focus_region(1)
            if pane.report_table.hasFocus():
                break
        assert pane.report_table.hasFocus()
        pane.search.setText("Такого студента нет")
        assert not pane.open_report_action.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_viewing_lessons_and_refreshing_scores_preserves_unapplied_calculation(course, tmp_path):  # noqa: F811
    app = QApplication.instance() or QApplication([])
    service, selected, roster = course
    first = service.courses.schedule(selected.id, roster.id, 1, date.today(), 1)
    second = service.courses.schedule(selected.id, roster.id, 2, date.today(), 3)
    window = MainWindow(
        service,
        AttendanceController(service, DemoReader),
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "office-preview.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    try:
        pane = window.grading_page
        baseline = service.grading.parameters(selected.id).neutral
        pane.parameters["neutral"].setValue(8.3)
        for lesson in (first, second):
            window.workspace.select_lesson(service.redesign.get_block(lesson.block_id).pair_ids[0])
            assert pane.parameters["neutral"].value() == 8.3
        pane.refresh()
        assert pane.parameters["neutral"].value() == 8.3
        assert service.grading.parameters(selected.id).neutral == baseline
    finally:
        window.close()
        app.processEvents()


def test_reader_events_menu_uses_viewed_lesson_with_another_registration_open(course, tmp_path):  # noqa: F811
    app = QApplication.instance() or QApplication([])
    service, selected, roster = course
    first = service.courses.schedule(selected.id, roster.id, 1, date.today(), 1)
    second = service.courses.schedule(selected.id, roster.id, 2, date.today(), 3)
    controller = AttendanceController(service, DemoReader)
    window = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "office-events.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    try:
        active = service.redesign.get_block(first.block_id)
        viewed = service.redesign.get_block(second.block_id)
        controller.open_session(active.id, set(active.pair_ids))
        window.workspace.select_lesson(viewed.pair_ids[0])
        window.show_active_log()
        assert window.logs_window.lesson.id == viewed.pair_ids[0]
        assert controller.active_lesson_id in active.pair_ids
    finally:
        window.close()
        app.processEvents()


def test_today_filter_uses_each_lessons_time_zone_at_midnight(course, tmp_path, monkeypatch):  # noqa: F811
    from zoneinfo import ZoneInfo

    from attendance_lab.presentation import lesson_workspace

    fixed = datetime(2026, 11, 9, 21, 30, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz or ZoneInfo("Europe/Minsk"))

    class LocalDay(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 10)

    monkeypatch.setattr(lesson_workspace, "datetime", Clock)
    monkeypatch.setattr(lesson_workspace, "date", LocalDay, raising=False)
    service, selected, roster = course
    utc_today = service.courses.schedule(
        selected.id, roster.id, 1, date(2026, 11, 9), 1, timezone="UTC"
    )
    minsk_today = service.courses.schedule(
        selected.id, roster.id, 2, date(2026, 11, 10), 1, timezone="Europe/Minsk"
    )
    service.courses.schedule(selected.id, roster.id, 3, date(2026, 11, 10), 3, timezone="UTC")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        service,
        AttendanceController(service, DemoReader),
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "office-midnight.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    try:
        assert window.workspace.today.isChecked()
        assert {
            window.workspace.blocks.itemData(i) for i in range(window.workspace.blocks.count())
        } == {utc_today.block_id, minsk_today.block_id}
    finally:
        window.close()
        app.processEvents()
