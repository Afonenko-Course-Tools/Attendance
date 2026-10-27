from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from attendance_lab.presentation.attendance_models import StudentModel, StudentProxy, StudentSearch


def test_numeric_group_sort_and_stable_identity():
    app = QApplication.instance() or QApplication([])
    students = [
        SimpleNamespace(
            student_id="ten",
            full_name="А",
            group_number=10,
            record_book_number="002",
            specialty="Ф",
            profile="",
            course=1,
        ),
        SimpleNamespace(
            student_id="two",
            full_name="Б",
            group_number=2,
            record_book_number="001",
            specialty="Х",
            profile="",
            course=1,
        ),
    ]
    model = StudentModel(students)
    proxy = StudentProxy()
    proxy.setSourceModel(model)
    proxy.sort(2)
    assert proxy.index(0, 0).data(Qt.ItemDataRole.UserRole) == "two"
    assert app is not None


def test_cached_fuzzy_search_handles_reordered_words_and_yo():
    people = [
        SimpleNamespace(
            student_id=str(i),
            full_name=f"Студент {i}",
            record_book_number=f"{i:04}",
            group_number=2,
            specialty="Физика",
            profile="",
            course=1,
        )
        for i in range(300)
    ]
    people.append(
        SimpleNamespace(
            student_id="chosen",
            full_name="Семёнов Иван",
            record_book_number="00999",
            group_number=10,
            specialty="Химия",
            profile="",
            course=1,
        )
    )
    search = StudentSearch(people)
    assert search.find("иван семенов")[0].student_id == "chosen"
    assert search.find("семенво")[0].student_id == "chosen"
    assert search.find("00999")[0].student_id == "chosen"


def test_session_scan_preserves_finder_and_exact_selected_ids(tmp_path):
    from datetime import date

    from attendance_lab.application.controller import AttendanceController
    from attendance_lab.application.service import AttendanceService
    from attendance_lab.infrastructure.csv_io import CsvFiles
    from attendance_lab.infrastructure.readers import DemoReader
    from attendance_lab.infrastructure.sqlite import SQLiteRepository
    from attendance_lab.presentation.admin import MainWindow

    app = QApplication.instance() or QApplication([])
    service = AttendanceService(SQLiteRepository(tmp_path / "ui.sqlite3", mode="demo"))
    api = service.redesign
    plan = api.preview_import(
        [
            "Внутренний № по списку",
            "ФИО",
            "Специальность",
            "Профилизация",
            "Курс",
            "Группа",
            "Номер зачётки",
        ],
        [["1", "Семёнов Иван", "Физика", "Физика", "1", "2", "00123"]],
        date.today().year,
    )
    assert not plan.errors
    api.apply_import(plan)
    discipline = service.save_discipline("Физика")
    year, group = api.list_years()[0], api.list_groups()[0]
    api.set_discipline_groups(discipline.id, year.id, {group.id})
    roster = api.save_roster(
        discipline.id, year.id, 1, "Лаборатория", "lab", 2, "groups", {group.id}, set()
    )
    block = api.schedule_block(roster.id, date.today(), 1)
    controller = AttendanceController(service, DemoReader)
    window = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "ui.sqlite3",
        default_timezone="UTC",
    )
    window.event_timer.stop()
    window.show()
    app.processEvents()
    for width, height in ((980, 720), (1366, 768)):
        window.resize(width, height)
        app.processEvents()
        workspace = window.workspace
        for button in (workspace.confirm_button, workspace.defer_button):
            position = button.mapTo(window, button.rect().topLeft())
            assert button.isVisible()
            assert position.y() >= 0
            assert position.y() + button.height() <= window.height()
        last_pair = workspace.proxy.index(0, workspace.model.columnCount() - 1)
        assert workspace.table.viewport().rect().contains(workspace.table.visualRect(last_pair))
        assert all(workspace.table.isColumnHidden(column) for column in (1, 3, 4, 5))
        workspace.show_context.setChecked(True)
        assert all(not workspace.table.isColumnHidden(column) for column in (1, 3, 4, 5))
        workspace.show_context.setChecked(False)
    assert window.history.model.contexts
    controller.connect_reader(controller.available_readers()[0])
    controller.open_session(block.id, {block.pair_ids[-1]})
    window.registration_changed()
    window.workspace.search.setText("семен")
    window.workspace.search.setCursorPosition(2)
    window.workspace.search.setFocus()
    window.workspace.table.selectRow(0)
    ids = window.workspace.selected_ids()
    controller.simulate("0011")
    window.process_events()
    assert window.workspace.search.text() == "семен"
    assert window.workspace.search.cursorPosition() == 2
    assert window.workspace.selected_ids() == ids
    assert window.workspace.pinned_uid == "0011"
    assert window.tabs.count() == 4
    window.workspace.pinned_uid = None
    window.workspace.pending_uids.clear()
    window.workspace.queue.clear()
    window.workspace.refresh()
    assert window.workspace.pinned_uid == "0011"
    student_id = api.list_student_contexts()[0].student_id
    service.bind_card("0011", student_id)
    window.history.update_evidence()
    cells = dict(
        zip(
            (pair.id for pair in window.history.model.report.lessons),
            window.history.model.report.rows[0].cells,
            strict=True,
        )
    )
    assert cells[block.pair_ids[-1]].state == "present"
    assert cells[block.pair_ids[0]].state != "present"
    controller.expand_session()
    window.registration_changed()
    assert all(cell.state == "present" for cell in window.history.model.report.rows[0].cells)
    window.workspace.table.setFocus()
    calls = []
    window.workspace.manual_block = lambda: calls.append("bulk")
    window.commands.confirm()
    assert calls == ["bulk"]
    window.close()
    service.close()


def test_history_targets_are_exact_and_filter_clears_them(tmp_path):
    from datetime import UTC, datetime, timedelta

    from PySide6.QtCore import QItemSelectionModel

    from attendance_lab.application.service import AttendanceService
    from attendance_lab.infrastructure.sqlite import SQLiteRepository
    from attendance_lab.presentation.history import HistoryPane

    app = QApplication.instance() or QApplication([])
    service = AttendanceService(SQLiteRepository(tmp_path / "history.sqlite3", mode="demo"))
    d = service.save_discipline("История")
    s = service.save_student("Студент", "2", "STABLE")
    now = datetime.now(UTC)
    p = service.save_lesson(d.id, now - timedelta(hours=2), now - timedelta(hours=1), "UTC")
    service.set_lesson_roster(p.id, {s.id})
    pane = HistoryPane(service)
    pane.refresh()
    selection = pane.matrix.selectionModel()
    selection.select(pane.proxy.index(0, 7), QItemSelectionModel.SelectionFlag.Select)
    assert pane.targets() == {(s.id, p.id)}
    pane.proxy.sort(0)
    assert pane.targets() == {(s.id, p.id)}
    pane.search.setText("hidden")
    assert pane.targets() == set()
    pane.close()
    service.close()
    assert app is not None


def test_import_preview_preserves_multiline_quoted_name(tmp_path):
    from attendance_lab.application.service import AttendanceService
    from attendance_lab.infrastructure.sqlite import SQLiteRepository
    from attendance_lab.presentation.preparation import ImportPreviewDialog

    app = QApplication.instance() or QApplication([])
    service = AttendanceService(SQLiteRepository(tmp_path / "import.sqlite3", mode="demo"))
    path = tmp_path / "people.csv"
    path.write_text(
        'Внутренний № по списку;ФИО;Специальность;Профилизация;Курс;Группа;Номер зачётки\n1;"Имя\nФамилия";Физика;Физика;1;1;00123\n',
        encoding="utf-8",
    )
    dialog = ImportPreviewDialog(service, lambda: None)
    dialog.path.setText(str(path))
    dialog.review()
    assert not dialog.plan.errors
    assert dialog.plan.rows[0].full_name == "Имя Фамилия"
    dialog.close()
    service.close()
    assert app is not None


def test_sort_ties_use_book_before_student_id():
    app = QApplication.instance() or QApplication([])
    students = [
        SimpleNamespace(
            student_id="A",
            full_name="Одинаковый",
            group_number=2,
            record_book_number="900",
            specialty="Ф",
            profile="",
            course=1,
        ),
        SimpleNamespace(
            student_id="Z",
            full_name="Одинаковый",
            group_number=2,
            record_book_number="001",
            specialty="Ф",
            profile="",
            course=1,
        ),
    ]
    model = StudentModel(students)
    proxy = StudentProxy()
    proxy.setSourceModel(model)
    proxy.sort(2)
    assert proxy.index(0, 0).data(Qt.ItemDataRole.UserRole) == "Z"
    proxy.set_sort_fields([(2, False), (1, True)])
    assert proxy.index(0, 0).data(Qt.ItemDataRole.UserRole) == "A"
    assert app is not None


def test_history_manual_revoke_restores_authoritative_baselines_and_selection(tmp_path):
    from datetime import UTC, datetime, timedelta

    from PySide6.QtCore import QItemSelectionModel

    from attendance_lab.application.service import AttendanceService
    from attendance_lab.infrastructure.sqlite import SQLiteRepository
    from attendance_lab.presentation.history import HistoryPane

    app = QApplication.instance() or QApplication([])
    service = AttendanceService(SQLiteRepository(tmp_path / "revoke.sqlite3", mode="demo"))
    discipline = service.save_discipline("Синтетический журнал")
    student = service.save_student("Выбранный студент", "2", "selected")
    other = service.save_student("Другой студент", "2", "other")
    now = datetime.now(UTC)
    first = service.save_lesson(
        discipline.id, now - timedelta(hours=4), now - timedelta(hours=3), "UTC"
    )
    second = service.save_lesson(
        discipline.id, now - timedelta(hours=2), now - timedelta(hours=1), "UTC"
    )
    service.set_lesson_roster(first.id, {student.id})
    service.set_lesson_roster(second.id, {other.id})
    service.open_lesson(first.id)
    pane = HistoryPane(service)
    pane.refresh()
    pane.show()
    app.processEvents()
    row = next(i for i, r in enumerate(pane.model.report.rows) if r.student.id == student.id)
    source = pane.model.index(row, 7)
    pane.matrix.selectionModel().select(
        pane.proxy.mapFromSource(source), QItemSelectionModel.SelectionFlag.Select
    )
    resets = []
    pane.model.modelReset.connect(lambda: resets.append(True))
    baseline = {r.student.id: r.cells for r in pane.model.report.rows}
    assert [cell.state for cell in baseline[student.id]] == ["pending", "outside"]
    batch = service.redesign.create_manual_batch(
        {(student.id, first.id), (student.id, second.id)}, "Синтетическая проверка"
    )
    pane.update_evidence()
    assert all(cell.state == "present" for cell in pane.model.report.rows[row].cells)
    assert pane.targets() == {(student.id, first.id)}
    service.redesign.revoke_manual_batch(batch.id)
    pane.update_evidence()
    assert {r.student.id: r.cells for r in pane.model.report.rows} == baseline
    assert pane.targets() == {(student.id, first.id)}
    assert not resets
    pane.close()
    service.close()


def test_enrollment_editor_preserves_current_selection_and_filters_year(monkeypatch):
    from PySide6.QtWidgets import QFormLayout, QWidget

    from attendance_lab.presentation.preparation import PreparationPane, checked

    app = QApplication.instance() or QApplication([])
    groups = [
        SimpleNamespace(id=11, academic_year_id=1, specialty_name="Ф", course=1, number=1),
        SimpleNamespace(id=12, academic_year_id=1, specialty_name="Ф", course=1, number=2),
        SimpleNamespace(id=21, academic_year_id=2, specialty_name="Ф", course=2, number=1),
    ]
    api = SimpleNamespace(
        list_years=lambda: [
            SimpleNamespace(id=1, start_year=2026),
            SimpleNamespace(id=2, start_year=2027),
        ],
        list_groups=lambda: groups,
        get_discipline_groups=lambda discipline, year: {
            (1, 1): {11},
            (2, 1): {12},
            (1, 2): {21},
        }.get((discipline, year), set()),
    )
    service = SimpleNamespace(
        redesign=api,
        list_disciplines=lambda: [SimpleNamespace(id=1, name="Ф"), SimpleNamespace(id=2, name="Х")],
    )
    pane = PreparationPane(service, lambda: None, QWidget(), QWidget())
    captured = []
    monkeypatch.setattr(pane, "run_form", lambda dialog, save: captured.append(dialog))
    pane.enrollment_dialog()
    dialog = captured[0]
    field = QFormLayout.ItemRole.FieldRole
    discipline = dialog.form.itemAt(0, field).widget()
    year = dialog.form.itemAt(1, field).widget()
    choices = dialog.form.itemAt(2, field).widget()
    assert choices.count() == 2
    assert checked(choices) == {11}
    discipline.setCurrentIndex(1)
    assert checked(choices) == {12}
    year.setCurrentIndex(1)
    assert choices.count() == 1
    assert checked(choices) == set()
    discipline.setCurrentIndex(0)
    assert checked(choices) == {21}
    pane.close()
    assert app is not None
