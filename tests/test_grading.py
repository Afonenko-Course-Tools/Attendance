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
    g.save_scores(course.id, {(student.student_id, work.id): 0})
    before = g.results(course.id)[0]
    assert before.final == 3.5
    saved = g.apply_parameters(course.id, replace(g.parameters(course.id), neutral=9))
    assert g.results(course.id)[0].final == 4.5
    assert service.repository.gr_scores(course.id)[(student.student_id, work.id)] == 0
    assert saved.version == 2
    assert g.apply_parameters(course.id, saved).version == 3
    with pytest.raises(AttendanceError):
        g.save_scores(course.id, {(student.student_id, work.id): 10, ("outside", work.id): 1})
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
    pane.tabs.setCurrentIndex(1)
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
    window.tabs.setCurrentWidget(window.calculation_pane)
    window.resize(980, 720)
    window.show()
    qapp.processEvents()
    pane = window.course_workspace.grading
    pane.parameters["neutral"].setValue(9)
    qapp.processEvents()
    assert pane.histogram.current[7] == 1
    assert pane.histogram.preview_counts[9] == 1
    assert window.tabs.isTabVisible(window.tabs.indexOf(window.calculation_pane))
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
    grading.save_scores(course.id, {(student.student_id, work.id): 10})
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
