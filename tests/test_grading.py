"""Synthetic assessment acceptance tests: original observations and course isolation."""

from dataclasses import replace
from datetime import date
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.grading import (
    CRITERIA,
    CalculationParameters,
    FitBound,
    ReportCard,
    WrittenScore,
    WrittenWork,
    calculate,
    distribution,
    fit_parameters,
    report_grade,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.grading_export import export_xlsx
from attendance_lab.infrastructure.sqlite import SQLiteRepository


def test_only_five_oral_criteria_active_without_written_and_levels_preserved():
    levels = tuple(0 if c.written else c.steps - 1 for c in CRITERIA)
    card = ReportCard(1, "student", 1, "Синтетическая тема", False, levels)
    parameters = CalculationParameters(criterion_weights=(100.0,) * 13 + (1.0,) * 5)
    assert report_grade(card, parameters) == 10
    assert report_grade(replace(card, written=True), parameters) < 5
    assert replace(replace(card, written=True), written=False).levels == levels
    for written in (False, True):
        assert report_grade(ReportCard(1, "student", 1, "Тема", written), parameters) == 7


def test_completeness_zero_and_different_scales():
    works = (WrittenWork(1, 1, "Контрольная", 100), WrittenWork(2, 1, "Самостоятельная", 20))
    p = CalculationParameters()
    missing = calculate("student", works, {1: 0}, None, False, p)
    assert missing.final is None and missing.missing == ("Самостоятельная",)
    complete = calculate("student", works, {1: 0, 2: 20}, None, False, p)
    assert complete.final == 5 and complete.mark == 5
    assert calculate("student", (), {}, None, False, p).final is None
    assert calculate("student", works, {1: 0, 2: 0}, None, False, p).final == 0


def test_fit_respects_locks_bounds_identical_inputs_and_observations():
    p = CalculationParameters()
    cards = tuple(ReportCard(1, str(i), 1, "Тема") for i in range(25))

    def evaluate(parameters):
        return tuple(calculate(c.student_id, (), {}, c, True, parameters) for c in cards)

    found = fit_parameters(
        p, evaluate, (FitBound("neutral", 4, 9), FitBound("spread", 1, 5, True)), 8, 0
    )
    assert found.neutral == 8 and found.spread == p.spread
    assert len({r.final for r in evaluate(found)}) == 1
    assert all(c.levels == ReportCard(1, "x", 1, "Тема").levels for c in cards)
    assert distribution(evaluate(found)).n == 25
    with pytest.raises(AttendanceError):
        replace(p, five_values=(-1, 0.5, 0, -0.5, 1))


@pytest.fixture
def graded_course(tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "synthetic.sqlite3"))
    headers = (
        "№",
        "ФИО",
        "Специальность",
        "Специализация/профилизация",
        "Курс",
        "Группа",
        "№ зачётки",
    )
    rows = [
        ["1", "Тест Один", "Тест", "Профиль", "2", "1", "00001"],
        ["2", "Тест Два", "Тест", "Профиль", "2", "1", "00002"],
        ["3", "Тест Три", "Тест", "Профиль", "2", "2", "00003"],
    ]
    service.redesign.apply_import(service.redesign.preview_import(headers, rows, 2026))
    group = service.redesign.list_groups()[0]
    discipline = service.save_discipline("Синтетический курс")
    course = service.courses.save_course(
        discipline.id, group.academic_year_id, 1, 2, "seminar", 1, "Курс", True
    )
    groups = {g.id for g in service.redesign.list_groups()}
    roster = service.courses.add_groups(course.id, groups)
    service.courses.save_topic(course.id, 1, "Программа")
    first = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    second = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 8), 1)
    yield service, course, first, second
    service.close()


def test_report_unique_student_course_and_topic_group_not_attendance(graded_course):
    service, course, first, second = graded_course
    grading = service.grading
    students = grading.students(course.id)
    one, two, other = students
    assert one.group_id == two.group_id and other.group_id != one.group_id
    card = ReportCard(course.id, one.student_id, first.id, "Общая тема")
    grading.save_report(card)
    grading.save_report(replace(card, lesson_id=second.id, written=True))
    assert len(service.repository.gr_reports(course.id)) == 1
    assert grading.report(course.id, one.student_id).lesson_id == second.id
    with pytest.raises(AttendanceError, match=one.full_name):
        grading.save_report(ReportCard(course.id, two.student_id, first.id, " общая ТЕМА "))
    grading.save_report(ReportCard(course.id, other.student_id, first.id, "Общая тема"))
    assert not any(
        e.active
        for lesson in service.courses.list_lessons(course.id)
        for pair in service.redesign.get_block(lesson.block_id).pair_ids
        for e in service.redesign.pair_evidence(pair)
    )


def test_recalculation_persistence_atomic_scores_and_flat_export(graded_course, tmp_path):
    service, course, first, _ = graded_course
    g = service.grading
    student = g.students(course.id)[0]
    g.save_report(ReportCard(course.id, student.student_id, first.id, "Тема"))
    work = g.add_work(course.id, "Контрольная", 20)
    g.save_scores(course.id, {(student.student_id, work.id): WrittenScore(0, "А")})
    before = g.results(course.id)[0]
    assert before.final == 3.5
    saved = g.apply_parameters(course.id, replace(g.parameters(course.id), neutral=9))
    assert g.results(course.id)[0].final == 4.5
    assert service.repository.gr_scores(course.id)[(student.student_id, work.id)] == 0
    assert saved.version == 2
    assert g.apply_parameters(course.id, saved).version == 3
    with pytest.raises(AttendanceError):
        g.save_scores(
            course.id,
            {(student.student_id, work.id): 10, ("outside", work.id): WrittenScore(1, "Б")},
        )
    assert service.repository.gr_scores(course.id)[(student.student_id, work.id)] == 0
    headers, rows = g.export_projection(course.id)
    assert rows[0][1] == student.record_book_number
    path = tmp_path / "synthetic.xlsx"
    export_xlsx(path, headers, rows)
    with ZipFile(path) as archive:
        sheets = [name for name in archive.namelist() if name.startswith("xl/worksheets/")]
        assert sheets == ["xl/worksheets/sheet1.xml"]
        root = ET.fromstring(archive.read(sheets[0]))
        assert len(root.findall(".//{*}row")) == 4
        assert b"00001" in archive.read(sheets[0])


def test_fitting_criterion_level_and_work_weights_preserves_order():
    parameters = CalculationParameters(work_weights=((1, 1.0),))
    levels = list(ReportCard(1, "s", 1, "Тема").levels)
    levels[13] = 4
    card = ReportCard(1, "s", 1, "Тема", False, tuple(levels))
    work = WrittenWork(1, 1, "Работа", 100)

    def evaluate(p):
        return (calculate("s", (work,), {1: 10}, card, True, p),)

    found = fit_parameters(
        parameters,
        evaluate,
        (
            FitBound("criterion:13", 0.1, 10),
            FitBound("five:3", 0, 1),
            FitBound("work:1", 0.1, 2, True),
        ),
        8,
        0,
    )
    assert found.criterion_weights[13] >= parameters.criterion_weights[13]
    assert found.work_weights == parameters.work_weights
    assert all(a <= b for a, b in zip(found.five_values, found.five_values[1:], strict=False))
    for invalid in (
        FitBound("neutral", 8, 4),
        FitBound("three:1", -1, 1),
        FitBound("criterion:99", 0.1, 1),
        FitBound("work:99", 0.1, 1),
    ):
        with pytest.raises(AttendanceError):
            fit_parameters(parameters, evaluate, (invalid,), 7, 1)


def test_subgroup_roster_does_not_include_unselected_classmates(graded_course):
    service, course, _, _ = graded_course
    student = service.grading.students(course.id)[0]
    subgroup_course = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 1, 2, "seminar", 1, "Только подгруппа", True
    )
    service.courses.add_groups(
        subgroup_course.id, {student.group_id}, "Подгруппа А", {student.student_id}
    )
    assert [s.student_id for s in service.grading.students(subgroup_course.id)] == [
        student.student_id
    ]
    assert service.grading.students(subgroup_course.id)[0].subgroup == "Подгруппа А"


def test_template_reuses_work_weights_by_title_and_scale(graded_course):
    service, course, _, _ = graded_course
    g = service.grading
    original = g.add_work(course.id, "Контрольная", 100)
    g.save_template("Вариант", replace(g.parameters(course.id), work_weights=((original.id, 3.0),)))
    second = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 2, 2, "seminar", 1, "Второй семестр", True
    )
    matching = g.add_work(second.id, "контрольная", 100)
    assert g.templates(second.id)["Вариант"].work_weights == ((matching.id, 3.0),)
    third = service.courses.save_course(
        course.discipline_id, course.academic_year_id, 2, 2, "seminar", 1, "Другая шкала", True
    )
    g.add_work(third.id, "Контрольная", 20)
    assert "Вариант" not in g.templates(third.id)


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    from attendance_lab.presentation.theme import apply_light_theme

    app = QApplication.instance() or QApplication([])
    apply_light_theme(app)
    yield app


def test_report_dialog_keeps_levels_saved_instance_keyboard_and_focus(
    graded_course, qapp, tmp_path
):
    from datetime import UTC, datetime

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from attendance_lab.presentation.grading import ReportCardDialog

    service, course, first, second = graded_course
    student = service.grading.students(course.id)[0]
    card = ReportCard(
        course.id, student.student_id, first.id, "Тема", True, tuple(c.steps - 1 for c in CRITERIA)
    )
    service.grading.save_report(card)
    dialog = ReportCardDialog(service.grading, course.id, student.student_id, second.id)
    dialog.show()
    qapp.processEvents()
    assert dialog.lesson.currentData() == first.id
    assert not dialog.lesson.isEnabled()
    dialog.written.setChecked(False)
    assert sum(slider.isEnabled() for slider in dialog.sliders) == 5
    assert tuple(slider.value() for slider in dialog.sliders) == card.levels
    dialog.sliders[13].setFocus()
    QTest.keyClick(dialog.sliders[13], Qt.Key.Key_Down)
    assert dialog.sliders[14].hasFocus()
    dialog.topic.setEditText("Незавершённый ввод")
    dialog.topic.lineEdit().setFocus()
    qapp.processEvents()
    block = service.redesign.get_block(first.block_id)
    session = service.redesign.open_session(block.id, set(block.pair_ids))
    service.redesign.record_session_scan(session.id, "0000ABCD", datetime.now(UTC))
    qapp.processEvents()
    assert dialog.topic.currentText() == "Незавершённый ввод"
    assert dialog.topic.lineEdit().hasFocus()
    assert dialog.lesson.currentData() == first.id
    dialog.grab().save(str(tmp_path / "synthetic-rubric.png"))
    dialog.save()
    saved = service.grading.report(course.id, student.student_id)
    assert saved.levels == card.levels and not saved.written and saved.lesson_id == first.id
    dialog.close()


def test_calculation_widget_preview_does_not_change_applied_or_original_scores(
    graded_course, qapp, tmp_path
):
    from attendance_lab.infrastructure.csv_io import CsvFiles
    from attendance_lab.presentation.grading import GradingPane

    service, course, first, _ = graded_course
    student = service.grading.students(course.id)[0]
    service.grading.save_report(ReportCard(course.id, student.student_id, first.id, "Тема"))
    pane = GradingPane(service, files=CsvFiles())
    pane.set_context(course.id, first.id)
    pane.resize(980, 720)
    pane.show()
    pane.tabs.setCurrentIndex(2)
    pane.parameters["neutral"].setValue(9)
    qapp.processEvents()
    assert service.grading.parameters(course.id).neutral == 7
    assert service.grading.results(course.id)[0].report == 7
    assert pane.candidate().neutral == 9
    pane.grab().save(str(tmp_path / "synthetic-calculation.png"))
    pane.close()


def test_export_preserves_first_actual_time_and_group_instance(graded_course):
    from datetime import UTC, datetime, timedelta

    service, course, first, _ = graded_course
    student = service.grading.students(course.id)[0]
    block = service.redesign.get_block(first.block_id)
    service.bind_card("0000AABB", student.student_id)
    session = service.redesign.open_session(block.id, set(block.pair_ids))
    actual = datetime(2026, 10, 20, 23, 58, 17, 123456, UTC)
    service.redesign.record_session_scan(session.id, "0000AABB", actual)
    service.redesign.record_session_scan(session.id, "0000AABB", actual + timedelta(minutes=4))
    headers, rows = service.grading.export_projection(course.id)
    attendance = rows[0][next(i for i, h in enumerate(headers) if h.startswith("Тема 1:"))]
    assert actual.isoformat() in attendance
    assert (actual + timedelta(minutes=4)).isoformat() not in attendance
    assert "2026-09-01" in attendance and "Группы 1, 2" in attendance
    assert len(service.repository.rd_pair_evidence(block.pair_ids[0])) == 2


def test_fitting_empty_degenerate_and_all_locked():
    from attendance_lab.domain.grading import GradeResult

    p = CalculationParameters()
    with pytest.raises(AttendanceError, match="Нет полных"):
        fit_parameters(p, lambda _: (), (), 7, 1)

    def evaluate(params):
        return (GradeResult("a", params.neutral, params.neutral, (), 1),) * 25

    assert fit_parameters(p, evaluate, (FitBound("neutral", 0, 10, True),), 10, 5) == p
    assert fit_parameters(p, evaluate, (), 10, 5) == p
    for target, deviation in ((float("nan"), 1), (7, float("inf")), (-1, 1), (7, 6)):
        with pytest.raises(AttendanceError):
            fit_parameters(p, evaluate, (), target, deviation)
    with pytest.raises(AttendanceError):
        fit_parameters(p, evaluate, (FitBound("neutral", 0, 10), FitBound("neutral", 0, 10)), 7, 1)
    stats = distribution(evaluate(p))
    assert stats.deviation == 0 and "одинаковы" in stats.normality


def test_xlsx_readable_styles_literal_text_and_atomic_failure(tmp_path, monkeypatch):
    import attendance_lab.infrastructure.grading_export as exporter

    path = tmp_path / "journal.xlsx"
    export_xlsx(path, ["ФИО", "№ зачётки", "Тема доклада"], [["=1+1", "00001", "Тема"]])
    original = path.read_bytes()
    with ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        for name in archive.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                ET.fromstring(archive.read(name))
        assert styles.find(".//{*}b") is not None
        assert styles.find(".//{*}fgColor").attrib["rgb"] == "FF003E78"
        assert all(16 <= float(c.attrib["width"]) <= 60 for c in sheet.findall(".//{*}col"))
        assert sheet.find(".//{*}col").attrib["width"] == "32"
        assert sheet.find(".//{*}pane").attrib["topLeftCell"] == "C2"
        assert sheet.find(".//{*}autoFilter").attrib["ref"] == "A1:C2"
        assert sheet.find(".//{*}f") is None
        assert sheet.find('.//{*}c[@r="A2"]').attrib["t"] == "inlineStr"

    def fail_replace(*args):
        raise OSError("Synthetic disk failure")

    monkeypatch.setattr(exporter.os, "replace", fail_replace)
    with pytest.raises(OSError):
        export_xlsx(path, ["ФИО"], [["Другое значение"]])
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".attendance-export-*"))
    with pytest.raises(AttendanceError):
        export_xlsx(path, ["ФИО"], [["Недопустимый\x01текст"]])
    assert path.read_bytes() == original


def test_main_window_calculation_integration_with_theme(graded_course, qapp, tmp_path):
    from attendance_lab.application.controller import AttendanceController
    from attendance_lab.infrastructure.csv_io import CsvFiles
    from attendance_lab.infrastructure.readers import DemoReader
    from attendance_lab.presentation.admin import MainWindow

    service, course, first, _ = graded_course
    student = service.grading.students(course.id)[0]
    service.grading.save_report(ReportCard(course.id, student.student_id, first.id, "Тема"))
    controller = AttendanceController(service, DemoReader)
    window = MainWindow(
        service,
        controller,
        files=CsvFiles(),
        demo=True,
        database_path=tmp_path / "synthetic.sqlite3",
        default_timezone="Europe/Minsk",
    )
    window.event_timer.stop()
    window.course_workspace.courses.setCurrentIndex(
        window.course_workspace.courses.findData(course.id)
    )
    window.tabs.setCurrentWidget(window.grading_page)
    window.grading_page.tabs.setCurrentIndex(0)
    window.resize(980, 720)
    window.show()
    qapp.processEvents()
    pane = window.course_workspace.grading
    pane.report_table.selectRow(0)
    qapp.processEvents()
    window.grab().save(str(tmp_path / "synthetic-main-reports.png"))
    pane.tabs.setCurrentIndex(2)
    pane.parameters["neutral"].setValue(9)
    qapp.processEvents()
    assert pane.histogram.current[7] == 1
    assert pane.histogram.preview_counts[9] == 1
    assert window.tabs.isTabVisible(window.tabs.indexOf(window.grading_page))
    assert pane.histogram.isVisible()
    assert pane.changes.isVisible()
    pane.calculation_scroll.ensureWidgetVisible(pane.histogram)
    qapp.processEvents()
    window.grab().save(str(tmp_path / "synthetic-main-calculation.png"))
    window.close()


def test_specific_work_distribution_includes_scored_but_incomplete_students(graded_course):
    service, course, first, _ = graded_course
    grading = service.grading
    student = grading.students(course.id)[0]
    work = grading.add_work(course.id, "Контрольная", 20)
    grading.save_scores(course.id, {(student.student_id, work.id): WrittenScore(10, "А")})
    assert grading.statistics(course.id).n == 0
    stats = grading.statistics(course.id, metric=work.id)
    assert stats.n == 1 and stats.incomplete == 2 and stats.mean == 5
    grading.save_report(ReportCard(course.id, student.student_id, first.id, "Тема"))
    preview = replace(grading.parameters(course.id), neutral=9)
    assert grading.statistics(course.id, metric="report").mean == 7
    assert grading.statistics(course.id, preview, metric="report").mean == 9
    assert grading.statistics(course.id, preview, metric=work.id).mean == 5


def test_deleted_occurrence_hidden_but_saved_report_history_survives(graded_course, qapp):
    from datetime import UTC, datetime

    from attendance_lab.presentation.grading import ReportCardDialog

    service, course, first, second = graded_course
    grading = service.grading
    student, another, _ = grading.students(course.id)
    block = service.redesign.get_block(first.block_id)
    service.bind_card("00001234", student.student_id)
    session = service.redesign.open_session(block.id, set(block.pair_ids))
    actual = datetime(2026, 9, 1, 6, 1, 23, 123456, UTC)
    service.redesign.record_session_scan(session.id, "00001234", actual)
    service.redesign.close_session(session.id)
    card = ReportCard(course.id, student.student_id, first.id, "Сохранённый доклад")
    grading.save_report(card)
    before_grade = grading.results(course.id)[0]
    before_evidence = tuple(service.repository.rd_pair_evidence(block.pair_ids[0]))
    deletion = service.lifecycle.delete_lesson(block.pair_ids[0], "Синтетическое удаление")
    assert first.id not in dict(grading.lessons(course.id))
    assert "(удалено)" in dict(grading.lessons(course.id, include_deleted=True))[first.id]
    assert not any(
        column[3] in block.pair_ids
        for column in service.repository.gr_attendance_columns(course.id)
    )
    assert grading.results(course.id)[0] == before_grade
    assert grading.report(course.id, student.student_id) == card
    dialog = ReportCardDialog(grading, course.id, student.student_id, second.id)
    assert dialog.lesson.currentData() == first.id
    assert "(удалено)" in dialog.lesson.currentText()
    assert not dialog.lesson.isEnabled()
    dialog.save()
    assert grading.report(course.id, student.student_id) == card
    new_dialog = ReportCardDialog(grading, course.id, another.student_id, first.id)
    assert new_dialog.lesson.findData(first.id) < 0
    new_dialog.close()
    with pytest.raises(AttendanceError, match="неудалённое"):
        grading.save_report(ReportCard(course.id, another.student_id, first.id, "Новый доклад"))
    headers, rows = grading.export_projection(course.id)
    attendance = next(i for i, h in enumerate(headers) if h.startswith("Тема 1:"))
    assert rows[0][attendance] == ""
    assert "(удалено)" in rows[0][headers.index("Занятие сдачи: дата, время, группа")]
    assert tuple(service.repository.rd_pair_evidence(block.pair_ids[0])) == before_evidence
    service.lifecycle.restore_deletion(deletion.id)
    assert first.id in dict(grading.lessons(course.id))
    assert "(удалено)" not in dict(grading.lessons(course.id))[first.id]
    headers, rows = grading.export_projection(course.id)
    assert actual.isoformat() in rows[0][attendance]
    assert tuple(service.repository.rd_pair_evidence(block.pair_ids[0])) == before_evidence


def test_grades_discoverable_before_modules_and_enable_reports_for_selected_course(
    graded_course, qapp, tmp_path
):
    from attendance_lab.presentation.grading import GradingPane

    service, course, first, _ = graded_course
    service.courses.save_course(
        course.discipline_id,
        course.academic_year_id,
        course.semester,
        course.study_course,
        course.kind,
        course.duration,
        course.name,
        False,
        course.id,
    )
    other = service.courses.save_course(
        course.discipline_id,
        course.academic_year_id,
        2,
        course.study_course,
        course.kind,
        course.duration,
        "Другой семестр",
        False,
    )
    pane = GradingPane(service)
    pane.set_context(course.id, first.id)
    pane.resize(980, 720)
    pane.show()
    qapp.processEvents()
    assert [pane.tabs.tabText(i) for i in range(pane.tabs.count())] == [
        "Доклады",
        "Контрольные работы",
        "Итоги",
    ]
    assert not pane.tabs.isVisible() and all(
        not pane.tabs.isTabEnabled(i) and not pane.tabs.isTabVisible(i)
        for i in range(pane.tabs.count())
    )
    assert pane.enable_reports_action.isVisible() and pane.enable_reports_action.isEnabled()
    assert pane.add_work_action.isVisible() and pane.add_work_action.isEnabled()
    assert not pane.open_report_action.isEnabled()
    assert not pane.paste_action.isEnabled()
    assert "пока нет докладов и контрольных" in pane.notice.text()
    assert not pane.report_table.isVisible()
    assert pane.calculation_settings.isHidden()
    assert pane.calculation_actions.isHidden()
    assert not pane.advanced_action.isVisible()
    assert not pane.coefficient_dialog.isVisible()
    pane.grab().save(str(tmp_path / "grades-empty-reports.png"))
    pane.enable_reports_action.trigger()
    enabled = service.courses.get_course(course.id)
    assert enabled == replace(course, reports_enabled=True)
    assert not service.courses.get_course(other.id).reports_enabled
    assert not pane.enable_reports_action.isVisible()
    assert pane.report_table.isVisible() and pane.report_table.rowCount() == 3
    assert [pane.tabs.isTabVisible(i) for i in range(pane.tabs.count())] == [True, False, True]
    assert pane.search.width() > pane.search.fontMetrics().horizontalAdvance(
        pane.search.placeholderText()
    )
    pane.tabs.setCurrentIndex(2)
    pane.settings_action.trigger()
    assert not pane.calculation_settings.isHidden()
    assert not pane.calculation_actions.isHidden()
    assert pane.advanced_action.isVisible()
    pane.settings_action.trigger()
    assert pane.calculation_settings.isHidden()
    assert pane.calculation_actions.isHidden()
    pane.tabs.setCurrentIndex(0)
    assert not pane.open_report_action.isEnabled()
    pane.report_table.selectRow(0)
    qapp.processEvents()
    assert pane.open_report_action.isEnabled()
    pane.close()


def test_control_creation_plain_grid_edit_and_rectangular_clipboard(
    graded_course, qapp, monkeypatch, tmp_path
):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QInputDialog

    from attendance_lab.presentation.grading import GradingPane

    service, course, first, _ = graded_course
    pane = GradingPane(service)
    pane.set_context(course.id, first.id)
    pane.tabs.setCurrentIndex(1)
    pane.resize(980, 720)
    pane.show()
    qapp.processEvents()
    assert pane.add_work_action.isEnabled() and not pane.paste_action.isEnabled()
    assert not pane.tabs.isTabVisible(1)
    assert not pane.journal.isVisible()
    titles = iter(["Контрольная 1", "Контрольная 2"])
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: (next(titles), True))
    monkeypatch.setattr(QInputDialog, "getDouble", lambda *args, **kwargs: (20.0, True))
    pane.add_work_action.trigger()
    pane.add_work_action.trigger()
    works = service.grading.works(course.id)
    assert len(works) == 2
    assert pane.journal.columnCount() == 5
    assert pane.journal.horizontalHeaderItem(1).text() == "Контрольная 1\nВариант"
    assert pane.journal.horizontalHeaderItem(2).text() == "Контрольная 1\nБаллы /20"
    assert pane.journal.isVisible()
    pane.journal.setCurrentCell(0, 1)
    pane.journal.setFocus()
    qapp.processEvents()
    assert pane.paste_action.isEnabled()
    QApplication.clipboard().setText("А\t5\tБ\t0\n\t\tА\t20")
    QTest.keyClick(pane.journal, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    scores = service.grading.scores(course.id)
    students = service.grading.students(course.id)
    assert scores[(students[0].student_id, works[0].id)] == 5
    assert scores[(students[0].student_id, works[1].id)] == 0
    assert (students[1].student_id, works[0].id) not in scores
    assert scores[(students[1].student_id, works[1].id)] == 20
    assert service.grading.variants(course.id) == {
        (students[0].student_id, works[0].id): "А",
        (students[0].student_id, works[1].id): "Б",
        (students[1].student_id, works[1].id): "А",
    }
    pane.journal.setCurrentCell(2, 1)
    pane.journal.item(2, 1).setText("В")
    assert (students[2].student_id, works[0].id) not in service.grading.variants(course.id)
    pane.journal.setCurrentCell(2, 2)
    pane.journal.item(2, 2).setText("10")
    assert service.grading.variants(course.id)[(students[2].student_id, works[0].id)] == "В"
    pane.journal.setCurrentCell(0, 2)
    pane.journal.item(0, 2).setText("0")
    assert service.grading.scores(course.id)[(students[0].student_id, works[0].id)] == 0
    assert pane.journal.currentRow() == 0 and pane.journal.currentColumn() == 2
    QApplication.clipboard().setText("1\t2")
    QTest.keyClick(pane.journal, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert service.grading.scores(course.id)[(students[0].student_id, works[0].id)] == 1
    assert service.grading.scores(course.id)[(students[0].student_id, works[1].id)] == 2
    assert service.grading.variants(course.id)[(students[0].student_id, works[1].id)] == "Б"
    pane.grab().save(str(tmp_path / "grades-controls.png"))
    pane.close()


def test_report_double_click_opens_selected_student_and_viewed_instance(graded_course, qapp):
    from attendance_lab.presentation.grading import GradingPane

    service, course, first, _ = graded_course
    pane = GradingPane(service)
    pane.set_context(course.id, first.id)
    pane.report_table.setCurrentCell(1, 0)
    pane.report_table.itemDoubleClicked.emit(pane.report_table.item(1, 0))
    dialog = pane.report_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.course_id == course.id
    assert dialog.student_id == service.grading.students(course.id)[1].student_id
    assert dialog.lesson.currentData() == first.id
    pane.close_report()
    pane.close()


def test_report_table_search_filters_and_orders_typed_dates(graded_course, qapp):
    from PySide6.QtCore import Qt

    from attendance_lab.presentation.grading import GradingPane

    service, course, first, second = graded_course
    one, two, *_ = service.grading.students(course.id)
    service.grading.save_report(ReportCard(course.id, one.student_id, second.id, "Тема 2"))
    service.grading.save_report(ReportCard(course.id, two.student_id, first.id, "Тема 1"))
    pane = GradingPane(service)
    pane.set_context(course.id, first.id)
    table = pane.report_table
    pane.report_status.setCurrentIndex(pane.report_status.findData(True))
    table.sortItems(3, Qt.SortOrder.AscendingOrder)
    visible = [row for row in range(table.rowCount()) if not table.isRowHidden(row)]
    assert [table.item(row, 2).text() for row in visible] == ["Тема 1", "Тема 2"]
    pane.search.setText(" ".join(reversed(one.full_name.split())) + " " + one.record_book_number)
    assert pane.report_count.text() == f"Показано 1 из {table.rowCount()}"
    pane.report_lesson.setCurrentIndex(pane.report_lesson.findData(first.id))
    assert pane.report_count.text() == f"Показано 0 из {table.rowCount()}"
    assert not pane.open_report_action.isEnabled()
    pane.close()


def test_pasting_scores_uses_sorted_visible_student_ids(graded_course, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from attendance_lab.presentation.grading import GradingPane

    service, course, first, _ = graded_course
    grading = service.grading
    work = grading.add_work(course.id, "Контрольная", 20)
    one, two, outside = grading.students(course.id)
    grading.save_scores(
        course.id,
        {
            (one.student_id, work.id): WrittenScore(1, "А"),
            (outside.student_id, work.id): WrittenScore(2, "Б"),
            (two.student_id, work.id): WrittenScore(3, "В"),
        },
    )
    pane = GradingPane(service)
    pane.set_context(course.id, first.id)
    pane.show()
    pane.tabs.setCurrentIndex(1)
    pane.search.setText("Тест 1")
    table = pane.journal
    table.sortItems(2, Qt.SortOrder.DescendingOrder)
    assert [i for i in range(table.rowCount()) if table.isRowHidden(i)] == [1]
    table.setCurrentCell(0, 2)
    QApplication.clipboard().setText("0\n19")
    pane.paste_scores()
    assert grading.scores(course.id) == {
        (one.student_id, work.id): 19,
        (outside.student_id, work.id): 2,
        (two.student_id, work.id): 0,
    }
    assert grading.variants(course.id) == {
        (one.student_id, work.id): "А",
        (outside.student_id, work.id): "Б",
        (two.student_id, work.id): "В",
    }
    pane.close()


@pytest.mark.parametrize(("key_name", "column"), [("Key_Delete", 2), ("Key_Backspace", 1)])
def test_clear_written_result_from_either_column(graded_course, qapp, key_name, column):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    from attendance_lab.presentation.grading import GradingPane

    service, course, lesson, _ = graded_course
    grading = service.grading
    first = grading.add_work(course.id, "Первая контрольная")
    second = grading.add_work(course.id, "Вторая контрольная")
    one, two, _ = grading.students(course.id)
    target = one.student_id, first.id
    saved = {(one.student_id, second.id): 59, (two.student_id, first.id): 87}
    grading.save_scores(
        course.id,
        {
            target: WrittenScore(0, "0"),
            **{key: WrittenScore(value, "Б") for key, value in saved.items()},
        },
    )
    pane = GradingPane(service)
    pane.set_context(course.id, lesson.id)
    pane.tabs.setCurrentIndex(1)
    pane.show()
    pane.activateWindow()
    table = pane.journal
    table.sortItems(2, Qt.SortOrder.DescendingOrder)
    table.setFocus()
    qapp.processEvents()

    def row_for(student_id):
        return next(
            row
            for row in range(table.rowCount())
            if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == student_id
        )

    row = row_for(one.student_id)
    table.setCurrentCell(row, 2)
    table.editItem(table.currentItem())
    editor = table.findChild(QLineEdit)
    assert editor is not None
    editor.setText("0.0")
    editor.setCursorPosition(3)
    QTest.keyClick(editor, getattr(Qt.Key, key_name))
    assert target in grading.scores(course.id)
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    table.setCurrentCell(row_for(one.student_id), column)
    QTest.keyClick(table, getattr(Qt.Key, key_name))

    assert grading.scores(course.id) == saved
    assert grading.variants(course.id) == dict.fromkeys(saved, "Б")
    pane.refresh()
    row = row_for(one.student_id)
    assert table.item(row, 1).text() == table.item(row, 2).text() == ""

    table.setCurrentCell(row_for(two.student_id), 3)
    table.currentItem().setText("Черновик")
    table.setCurrentCell(row_for(two.student_id), 3)
    QTest.keyClick(table, getattr(Qt.Key, key_name))
    assert table.item(row_for(two.student_id), 3).text() == ""
    assert grading.scores(course.id) == saved
    pane.close()


def test_new_score_requires_explicit_variant_and_can_be_filtered_in_statistics(
    graded_course, qapp, monkeypatch
):
    from PySide6.QtWidgets import QApplication, QInputDialog

    from attendance_lab.presentation.grading import GradingPane

    service, course, first, _ = graded_course
    grading = service.grading
    work = grading.add_work(course.id, "Контрольная", 20)
    student, other, _ = grading.students(course.id)
    grading.save_scores(course.id, {(other.student_id, work.id): WrittenScore(20, "А")})
    pane = GradingPane(service)
    pane.set_context(course.id, first.id)
    pane.show()
    pane.tabs.setCurrentIndex(1)
    pane.journal.setCurrentCell(0, 2)
    QApplication.clipboard().setText("10")
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("", False))
    pane.paste_scores()
    assert (student.student_id, work.id) not in grading.scores(course.id)

    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Б", True))
    pane.paste_scores()
    assert grading.variants(course.id)[(student.student_id, work.id)] == "Б"
    pane.tabs.setCurrentIndex(2)
    pane.metric_selector.setCurrentIndex(pane.metric_selector.findData(work.id))
    pane.variant_selector.setCurrentIndex(pane.variant_selector.findData("Б"))
    assert pane.preview_request().variant == "Б"
    pane.refresh()
    assert pane.preview_request().variant == "Б"
    assert "среднее 5.00" in pane.statistics.text()
    assert pane.variant_stats.rowCount() == 2
    pane.close()


def test_lesson_picker_bounds_long_list_and_finds_by_date_topic_and_group(qapp):
    from datetime import timedelta

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    from attendance_lab.presentation.lesson_picker import LessonPickerDialog, LessonPickerOption

    first = date(2026, 9, 1)
    entries = [
        LessonPickerOption(
            i,
            first + timedelta(days=i),
            "13:50–15:10",
            "Резервирование" if i == 150 else "Сети",
            "7ПИ" if i == 150 else "2РФ",
        )
        for i in range(1, 180)
    ]
    picker = LessonPickerDialog(entries, allow_all=True)
    picker.show()
    qapp.processEvents()
    assert picker.lessons.rowCount() == 179
    assert picker.lessons.verticalScrollBar().maximum() > 0
    assert picker.height() < picker.screen().availableGeometry().height()
    target = entries[149]
    picker.search.setText(f"резервироване 7пи {target.date:%d.%m.%Y}")
    assert picker.lessons.rowCount() == 1
    assert picker.lessons.item(0, 2).text() == "Резервирование"
    QTest.keyClick(picker.search, Qt.Key.Key_Down)
    assert picker.lessons.hasFocus()
    QTest.keyClick(picker.lessons, Qt.Key.Key_Return)
    assert picker.result() == QDialog.DialogCode.Accepted and picker.lesson_id == target.id

    picker = LessonPickerDialog(entries, selected_id=target.id, allow_all=True)
    picker.all_button.click()
    assert picker.result() == QDialog.DialogCode.Accepted and picker.lesson_id is None
