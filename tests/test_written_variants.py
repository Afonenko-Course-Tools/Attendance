import sqlite3
from contextlib import closing

import pytest

from attendance_lab.application.grading_preview import (
    CalculationDraft,
    GradingFitRequest,
    GradingPreviewRequest,
    GradingPreviewService,
)
from attendance_lab.application.quick_grading import GradingActions
from attendance_lab.application.service import AttendanceService
from attendance_lab.domain.grading import (
    CalculationParameters,
    FitBound,
    GradeResult,
    WrittenScore,
    statistical_moments,
    variant_balance,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.infrastructure.sqlite import SQLiteRepository


@pytest.fixture
def assessment(tmp_path):
    service = AttendanceService(SQLiteRepository(tmp_path / "written-scores.sqlite3"))
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
        [str(number), f"Студент {number:02}", "Тест", "Профиль", "2", "1", f"{number:05}"]
        for number in range(1, 23)
    ]
    service.redesign.apply_import(service.redesign.preview_import(headers, rows, 2026))
    discipline = service.save_discipline("Варианты контрольной")
    group = service.redesign.list_groups()[0]
    course = service.courses.save_course(
        discipline.id, group.academic_year_id, 1, 2, "seminar", 1, "Курс"
    )
    service.courses.add_groups(course.id, {group.id})
    work = service.grading.add_work(course.id, "Контрольная")
    students = service.grading.students(course.id)
    yield service, course.id, work.id, students
    service.close()


def test_score_validation_is_atomic_and_clear_removes_variant(assessment):
    service, course_id, work_id, (one, two, *_) = assessment
    grading = service.grading
    first, second = (one.student_id, work_id), (two.student_id, work_id)
    with pytest.raises(AttendanceError, match="Укажите вариант"):
        grading.save_scores(course_id, {first: WrittenScore(0, "1"), second: 50})
    assert grading.scores(course_id) == grading.variants(course_id) == {}
    with pytest.raises(AttendanceError, match="Укажите вариант"):
        WrittenScore(0, "  ")

    grading.save_scores(course_id, {first: WrittenScore(0, "  А  ")})
    grading.save_scores(course_id, {first: 24})
    assert grading.scores(course_id) == {first: 24}
    assert grading.variants(course_id) == {first: "А"}
    headers, rows = grading.export_projection(course_id)
    assert rows[0][headers.index("Контрольная — Вариант")] == "А"
    grading.save_scores(course_id, {first: None})
    assert grading.scores(course_id) == grading.variants(course_id) == {}


def test_preview_balances_the_fitting_objective_with_raw_scores_unchanged(assessment):
    service, course_id, work_id, students = assessment
    grading = service.grading
    records = [
        WrittenScore(80, "1")
        if index < 15
        else WrittenScore(20, "2")
        if index < 20
        else WrittenScore(50, "3")
        for index in range(len(students))
    ]
    original = {
        (student.student_id, work_id): score
        for student, score in zip(students, records, strict=True)
    }
    grading.save_scores(course_id, original)
    preview = GradingPreviewService(grading)
    request = GradingPreviewRequest(
        course_id, CalculationDraft(7, 3, 1, 0, 1), metric=work_id, variant="2"
    )
    baseline = preview.preview(request)
    assert baseline.statistics.n == 5 and baseline.statistics.mean == 2
    assert [
        (row.variant, row.n, row.mean, row.fit_eligible) for row in baseline.variant_statistics
    ] == [
        ("1", 15, 8, True),
        ("2", 5, 2, True),
        ("3", 2, 5, False),
    ]
    assert baseline.fit_statistics.balanced
    assert baseline.fit_statistics.work_ids == (work_id,)
    assert baseline.fit_statistics.n == 22
    assert baseline.fit_statistics.mean == pytest.approx(5)
    assert grading.statistics(course_id).mean == pytest.approx(140 / 22)

    fitted = preview.fit(
        GradingFitRequest(
            request,
            (FitBound("final_offset", -1, 1),),
            6,
            baseline.fit_statistics.deviation,
        )
    )
    assert fitted.parameters.final_offset == pytest.approx(1)
    assert fitted.fit_statistics.mean == pytest.approx(6)
    assert fitted.statistics.mean == 2
    assert grading.parameters(course_id) == CalculationParameters()
    assert grading.scores(course_id) == {key: value.score for key, value in original.items()}


def test_fit_falls_back_when_only_one_variant_is_well_covered():
    results = tuple(GradeResult(str(i), None, float(i), (), 1) for i in range(10))
    variants = {(str(i), 1): "1" if i < 6 else "2" for i in range(10)}
    balance = variant_balance(results, variants)
    assert balance.work_ids == ()
    assert statistical_moments(results, balance.weights) == statistical_moments(results)


def test_balancing_averages_work_weights_without_reweighting_sparse_or_legacy_rows():
    results = tuple(GradeResult(str(i), None, float(i % 11), (), 1) for i in range(22))
    variants = {(str(i), 1): "1" if i < 15 else "2" for i in range(20)}
    variants.update({(str(i), 2): "1" if i < 10 else "2" for i in range(20)})
    variants["20", 1] = "3"
    balance = variant_balance(results, variants)

    assert balance.work_ids == (1, 2)
    assert balance.weights["0"] == pytest.approx((2 / 3 + 1) / 2)
    assert balance.weights["15"] == pytest.approx((2 + 1) / 2)
    assert balance.weights.get("20", 1) == balance.weights.get("21", 1) == 1
    assert sum(balance.weights.get(result.student_id, 1) for result in results) == pytest.approx(22)


def test_v6_score_migrates_without_guess_and_requires_variant_when_edited(assessment, tmp_path):
    service, course_id, work_id, (student, *_) = assessment
    key = student.student_id, work_id
    database = tmp_path / "schema-v6.sqlite3"
    with (
        closing(sqlite3.connect(service.repository.path)) as source,
        closing(sqlite3.connect(database)) as copy,
    ):
        source.backup(copy)
        copy.execute("DROP TABLE course_score_variants")
        copy.execute(
            "INSERT INTO course_scores(work_id, student_id, score) VALUES(?,?,?)",
            (work_id, student.student_id, 60),
        )
        copy.execute("PRAGMA user_version=6")
        copy.commit()

    migrated = AttendanceService(SQLiteRepository(database))
    try:
        grading = migrated.grading
        assert grading.scores(course_id) == {key: 60}
        assert grading.variants(course_id) == {}
        assert grading.results(course_id)[0].final == 6
        assert grading.statistics(course_id, metric=work_id, variant="").n == 1
        assert grading.statistics(course_id, metric=work_id, variant="1").n == 0
        actions = GradingActions(migrated)
        snapshot = actions.snapshot(course_id)
        cell = snapshot["scoreRows"][0]["cells"][0]
        assert cell["value"] == 60 and cell["variant"] is None and cell["legacy"]
        assert snapshot["variants"] == [
            {"work_id": work_id, "variant": "", "label": "Вариант не указан"}
        ]
        [legacy] = snapshot["preview"]["variant_statistics"]
        assert legacy["variant"] is None and legacy["n"] == 1 and not legacy["fit_eligible"]
        payload = {
            "course_id": course_id,
            "student_id": student.student_id,
            "work_id": work_id,
            "value": 61,
        }
        with pytest.raises(AttendanceError, match="Укажите вариант"):
            actions.execute("grading.score", payload)
        result = actions.execute("grading.score", dict(payload, variant="2"))
        assert (result["student_id"], result["work_id"]) == key
        assert grading.scores(course_id) == {key: 61}
        assert grading.variants(course_id) == {key: "2"}
    finally:
        migrated.close()
