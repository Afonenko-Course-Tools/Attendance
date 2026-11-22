"""Application preview contracts run without importing Qt or changing stored work."""

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from attendance_lab.application.grading_preview import (
    CalculationDraft,
    GradingFitRequest,
    GradingPreviewRequest,
    GradingPreviewService,
    ReportCardDraft,
    candidate_parameters,
    preview_report_card,
)
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.grading import (
    CRITERIA,
    CalculationParameters,
    FitBound,
    ReportCard,
    WrittenScore,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def course_data(tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "preview.sqlite3"))
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
    groups = service.redesign.list_groups()
    discipline = service.save_discipline("Синтетический курс")
    course = service.courses.save_course(
        discipline.id, groups[0].academic_year_id, 1, 2, "seminar", 1, "Курс", True
    )
    roster = service.courses.add_groups(course.id, {group.id for group in groups})
    service.courses.save_topic(course.id, 1, "Программа")
    lesson = service.courses.schedule(course.id, roster.id, 1, date(2026, 9, 1), 1)
    grading = service.grading
    yield grading, course, lesson, grading.students(course.id)
    service.close()


def draft(**changes):
    return replace(CalculationDraft(7, 3, 1, 0, 1), **changes)


def test_candidate_overlays_typed_draft_and_retains_unedited_parameters():
    saved = CalculationParameters(version=4, work_weights=((10, 2.0),))
    candidate = candidate_parameters(
        saved,
        draft(neutral=8, coefficients=(("criterion:0", 2), ("five:3", 0.7), ("work:11", 3))),
    )
    assert candidate.version == 4 and candidate.neutral == 8
    assert candidate.criterion_weights == (2,) + (1,) * 17
    assert candidate.five_values == (-1, -0.5, 0, 0.7, 1)
    assert dict(candidate.work_weights) == {10: 2, 11: 3}
    assert saved == CalculationParameters(version=4, work_weights=((10, 2.0),))
    with pytest.raises(FrozenInstanceError):
        candidate.neutral = 9


@pytest.mark.parametrize(
    "coefficients",
    [
        (("criterion:-1", 1),),
        (("criterion:18", 1),),
        (("unknown:0", 1),),
        (("work:not-an-id", 1),),
        (("neutral", 1),),
        (("criterion:0", 1), ("criterion:0", 2)),
        (("five:2", 1),),
        (("five:3", -1),),
        (("work:1", float("nan")),),
    ],
)
def test_invalid_coefficients_raise_domain_error(coefficients):
    with pytest.raises(AttendanceError):
        candidate_parameters(CalculationParameters(), draft(coefficients=coefficients))


def test_preview_keeps_baseline_revision_ids_and_observations(course_data, monkeypatch):
    grading, course, lesson, students = course_data
    student = students[0]
    grading.save_report(ReportCard(course.id, student.student_id, lesson.id, "Тема"))
    work = grading.add_work(course.id, "Работа", 20)
    grading.save_scores(course.id, {(student.student_id, work.id): WrittenScore(0, "1")})
    original = grading.students
    # A projection joins by ID even when another query supplies a different row order.
    monkeypatch.setattr(grading, "students", lambda course_id: tuple(reversed(original(course_id))))
    request = GradingPreviewRequest(course.id, draft(neutral=9), revision=12)
    preview = GradingPreviewService(grading).preview(request)
    assert (preview.course_id, preview.revision, preview.parameter_version) == (course.id, 12, 1)
    assert preview.baseline_statistics.mean == 3.5
    assert preview.statistics.mean == 4.5
    assert preview.statistics.incomplete == 2
    changed = next(change for change in preview.changes if change.student_id == student.student_id)
    assert changed.full_name == student.full_name
    assert changed.baseline.final == 3.5 and changed.candidate.final == 4.5
    assert changed.readiness == "Все работы оценены"
    assert all(
        change.baseline.student_id == change.candidate.student_id for change in preview.changes
    )
    assert any(change.readiness.startswith("Не хватает:") for change in preview.changes)
    assert grading.parameters(course.id) == CalculationParameters()
    assert grading.scores(course.id) == {(student.student_id, work.id): 0}
    assert (
        grading.report(course.id, student.student_id).levels
        == ReportCard(course.id, student.student_id, lesson.id, "Тема").levels
    )


def test_statistics_slice_and_work_metric_do_not_filter_course_changes(course_data):
    grading, course, _, students = course_data
    student = students[0]
    work = grading.add_work(course.id, "Работа", 20)
    grading.save_scores(course.id, {(student.student_id, work.id): WrittenScore(10, "1")})
    previews = GradingPreviewService(grading)
    request = GradingPreviewRequest(
        course.id,
        draft(final_offset=4, final_scale=2),
        group_id=student.group_id,
        specialty=student.specialty,
        metric=work.id,
    )
    preview = previews.preview(request)
    assert preview.statistics.mean == preview.baseline_statistics.mean == 5
    assert preview.statistics.incomplete == 1
    assert len(preview.changes) == len(students) == 3
    assert all(change.candidate.final is None for change in preview.changes)
    empty = previews.preview(replace(request, specialty="Нет такой специальности"))
    assert empty.statistics.n == empty.statistics.incomplete == 0
    with pytest.raises(AttendanceError, match="Работа для статистики"):
        previews.preview(replace(request, metric=work.id + 100))


def test_report_statistics_and_fit_use_common_formula_without_saving(course_data):
    grading, course, lesson, students = course_data
    for student in students:
        grading.save_report(ReportCard(course.id, student.student_id, lesson.id, student.full_name))
    previews = GradingPreviewService(grading)
    request = GradingPreviewRequest(course.id, draft(), revision=3, metric="report")
    fitted = previews.fit(GradingFitRequest(request, (FitBound("neutral", 4, 9),), 8, 0))
    assert fitted.parameters.neutral == fitted.statistics.mean == 8
    assert fitted.baseline_statistics.mean == 7
    assert fitted.revision == 3
    assert grading.parameters(course.id).neutral == 7
    assert grading.results(course.id)[0].report == 7
    report = previews.preview(replace(request, draft=draft(neutral=9)))
    assert report.statistics.mean == 9


def test_failed_fit_or_preview_preserves_saved_parameters(course_data):
    grading, course, _, _ = course_data
    previews = GradingPreviewService(grading)
    request = GradingPreviewRequest(course.id, draft())
    with pytest.raises(AttendanceError, match="Нет полных"):
        previews.fit(GradingFitRequest(request, (FitBound("neutral", 4, 9),), 8, 0))
    with pytest.raises(AttendanceError):
        previews.preview(replace(request, draft=draft(spread=-1)))
    assert grading.parameters(course.id) == CalculationParameters()
    assert previews.preview(request).statistics.incomplete == 3


def test_fit_targets_whole_course_while_statistics_show_selected_group(course_data):
    grading, course, lesson, students = course_data
    for student in students:
        levels = (
            tuple(criterion.steps - 1 for criterion in CRITERIA)
            if student.group_id != students[0].group_id
            else ReportCard(course.id, student.student_id, lesson.id, "Тема").levels
        )
        grading.save_report(
            ReportCard(course.id, student.student_id, lesson.id, student.full_name, levels=levels)
        )
    request = GradingPreviewRequest(course.id, draft(), group_id=students[0].group_id)
    fitted = GradingPreviewService(grading).fit(
        GradingFitRequest(request, (FitBound("neutral", 4, 9),), 8, 2**0.5)
    )
    assert fitted.parameters.neutral == 7
    assert fitted.statistics.mean == 7 and fitted.statistics.n == 2
    assert len(fitted.changes) == 3


def test_report_card_preview_accepts_unfinished_fields_and_retains_hidden_levels():
    levels = tuple(0 if criterion.written else criterion.steps - 1 for criterion in CRITERIA)
    card = ReportCardDraft(3, "stable-id", None, "", False, levels)
    parameters = CalculationParameters(version=4)
    preview = preview_report_card(card, parameters)
    assert (preview.course_id, preview.student_id, preview.parameter_version) == (3, "stable-id", 4)
    assert preview.grade == 10
    assert preview_report_card(replace(card, written=True), parameters).grade < 7
    assert preview_report_card(replace(card, topic="  "), parameters).grade == 10
    assert preview_report_card(card, parameters).grade == 10
    assert card.levels == levels
