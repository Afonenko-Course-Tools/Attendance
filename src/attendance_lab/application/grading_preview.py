"""Read-only assessment drafts and projections shared by presentation adapters.

Revisions identify a caller's draft, not a database revision. A future async
adapter can discard a response whose course or draft revision is no longer
current. Parameter versions identify the saved baseline used by the calculation.
"""

from dataclasses import dataclass, replace

from attendance_lab.application.grading import GradingInputs, GradingService, VariantStatistics
from attendance_lab.domain.grading import (
    MINIMUM_VARIANT_FIT_SIZE,
    CalculationParameters,
    Distribution,
    FitBound,
    GradeResult,
    ReportCard,
    VariantBalance,
    fit_parameters,
    report_grade,
    statistical_moments,
    variant_balance,
)
from attendance_lab.domain.models import AttendanceError


@dataclass(frozen=True, slots=True)
class CalculationDraft:
    neutral: float
    spread: float
    report_weight: float
    final_offset: float
    final_scale: float
    coefficients: tuple[tuple[str, float], ...] = ()


def candidate_parameters(
    saved: CalculationParameters, draft: CalculationDraft
) -> CalculationParameters:
    """Apply editable fields without changing observations or saved parameters."""
    weights = list(saved.criterion_weights)
    three, five = list(saved.three_values), list(saved.five_values)
    works = dict(saved.work_weights)
    if len(dict(draft.coefficients)) != len(draft.coefficients):
        raise AttendanceError("Коэффициент указан несколько раз.")
    for key, value in draft.coefficients:
        try:
            kind, index_text = key.split(":")
            index = int(index_text)
            if index < 0:
                raise ValueError()
            if kind == "work":
                works[index] = value
            else:
                {"criterion": weights, "three": three, "five": five}[kind][index] = value
        except (ValueError, KeyError, IndexError) as exc:
            raise AttendanceError("Неизвестный коэффициент расчёта.") from exc
    return replace(
        saved,
        neutral=draft.neutral,
        spread=draft.spread,
        report_weight=draft.report_weight,
        final_offset=draft.final_offset,
        final_scale=draft.final_scale,
        criterion_weights=tuple(weights),
        three_values=tuple(three),
        five_values=tuple(five),
        work_weights=tuple(works.items()),
    )


@dataclass(frozen=True, slots=True)
class GradingPreviewRequest:
    course_id: int
    draft: CalculationDraft
    revision: int = 0
    group_id: int | None = None
    specialty: str | None = None
    metric: str | int | None = None
    variant: str | None = None


@dataclass(frozen=True, slots=True)
class GradingChange:
    student_id: str
    full_name: str
    baseline: GradeResult
    candidate: GradeResult

    @property
    def readiness(self) -> str:
        if self.candidate.missing:
            return "Не хватает: " + ", ".join(self.candidate.missing)
        return (
            "Все работы оценены"
            if self.candidate.final is not None
            else "Добавьте доклады или контрольную"
        )


@dataclass(frozen=True, slots=True)
class FitSampleStatistics:
    n: int
    balanced: bool
    work_ids: tuple[int, ...]
    minimum_variant_size: int
    baseline_mean: float | None
    baseline_deviation: float | None
    mean: float | None
    deviation: float | None


@dataclass(frozen=True, slots=True)
class GradingPreview:
    course_id: int
    revision: int
    baseline_parameters: CalculationParameters
    parameters: CalculationParameters
    baseline: tuple[GradeResult, ...]
    candidate: tuple[GradeResult, ...]
    baseline_statistics: Distribution
    statistics: Distribution
    changes: tuple[GradingChange, ...]
    variant_statistics: tuple[VariantStatistics, ...]
    fit_statistics: FitSampleStatistics

    @property
    def parameter_version(self) -> int:
        return self.baseline_parameters.version


@dataclass(frozen=True, slots=True)
class GradingFitRequest:
    preview: GradingPreviewRequest
    bounds: tuple[FitBound, ...]
    target_mean: float
    target_deviation: float


class GradingPreviewService:
    def __init__(self, grading: GradingService):
        self.grading = grading

    def candidate(self, course_id: int, draft: CalculationDraft) -> CalculationParameters:
        return candidate_parameters(self.grading.parameters(course_id), draft)

    def preview(
        self, request: GradingPreviewRequest, *, inputs: GradingInputs | None = None
    ) -> GradingPreview:
        inputs = inputs if inputs is not None else self.grading.inputs(request.course_id)
        candidate = candidate_parameters(inputs.parameters, request.draft)
        return self._project(request, inputs, candidate)

    def fit(self, request: GradingFitRequest) -> GradingPreview:
        """Fit the whole course even when its displayed statistics use a slice."""
        inputs = self.grading.inputs(request.preview.course_id)
        evaluate = inputs.evaluator()
        baseline = evaluate(inputs.parameters)
        balance = variant_balance(baseline, inputs.variants)
        found = fit_parameters(
            candidate_parameters(inputs.parameters, request.preview.draft),
            evaluate,
            request.bounds,
            request.target_mean,
            request.target_deviation,
            observation_weights=balance.weights,
        )
        return self._project(request.preview, inputs, found, baseline=baseline, balance=balance)

    def _project(
        self,
        request: GradingPreviewRequest,
        inputs: GradingInputs,
        candidate: CalculationParameters,
        *,
        baseline: tuple[GradeResult, ...] | None = None,
        balance: VariantBalance | None = None,
    ) -> GradingPreview:
        evaluate = inputs.evaluator()
        saved = inputs.parameters
        baseline = evaluate(saved) if baseline is None else baseline
        results = baseline if candidate == saved else evaluate(candidate)
        balance = variant_balance(baseline, inputs.variants) if balance is None else balance
        baseline_by_id = {result.student_id: result for result in baseline}
        students = {student.student_id: student for student in inputs.students}
        selection = dict(
            group_id=request.group_id, specialty=request.specialty, metric=request.metric
        )
        baseline_mean, baseline_deviation = statistical_moments(baseline, balance.weights)
        mean, deviation = statistical_moments(results, balance.weights)
        return GradingPreview(
            course_id=request.course_id,
            revision=request.revision,
            baseline_parameters=saved,
            parameters=candidate,
            baseline=baseline,
            candidate=results,
            baseline_statistics=inputs.statistics(
                baseline, saved, variant=request.variant, **selection
            ),
            statistics=inputs.statistics(results, candidate, variant=request.variant, **selection),
            changes=tuple(
                GradingChange(
                    result.student_id,
                    students[result.student_id].full_name,
                    baseline_by_id[result.student_id],
                    result,
                )
                for result in results
            ),
            variant_statistics=inputs.variant_statistics(baseline, balance, **selection),
            fit_statistics=FitSampleStatistics(
                sum(result.final is not None for result in baseline),
                bool(balance.work_ids),
                balance.work_ids,
                MINIMUM_VARIANT_FIT_SIZE,
                baseline_mean,
                baseline_deviation,
                mean,
                deviation,
            ),
        )


@dataclass(frozen=True, slots=True)
class ReportCardDraft:
    course_id: int
    student_id: str
    lesson_id: int | None
    topic: str
    written: bool
    levels: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ReportCardPreview:
    course_id: int
    student_id: str
    parameter_version: int
    grade: float


def preview_report_card(
    draft: ReportCardDraft, parameters: CalculationParameters
) -> ReportCardPreview:
    """An unfinished topic or lesson does not prevent rubric-only preview."""
    card = ReportCard(
        draft.course_id,
        draft.student_id,
        draft.lesson_id or 0,
        draft.topic.strip() or "Предпросмотр",
        draft.written,
        draft.levels,
    )
    return ReportCardPreview(
        draft.course_id, draft.student_id, parameters.version, report_grade(card, parameters)
    )
