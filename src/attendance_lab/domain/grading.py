"""Pure, versioned course assessment; observations are never mutated by fitting."""

from dataclasses import dataclass, replace
from math import exp, isfinite
from statistics import fmean, pstdev

from attendance_lab.domain.models import AttendanceError


@dataclass(frozen=True, slots=True)
class Criterion:
    key: str
    title: str
    steps: int = 5
    written: bool = True


CRITERIA = tuple(
    Criterion(str(i), title, steps, i < 13)
    for i, (title, steps) in enumerate(
        (
            ("Введение и заключение", 3),
            ("Последовательность и согласованность", 5),
            ("Нейрогенерация и проработка текста", 5),
            ("Термины и сокращения", 3),
            ("Академический стиль", 3),
            ("Уровень фактов", 5),
            ("Практическая часть", 5),
            ("Ссылки в тексте", 3),
            ("Актуальность источников", 5),
            ("Авторитетность источников", 5),
            ("Объём", 3),
            ("Список литературы", 3),
            ("Замечания к оформлению", 3),
            ("Презентация", 5),
            ("Изложение", 5),
            ("Термины и определения — ответы", 5),
            ("Базовые знания — ответы", 5),
            ("Содержательные вопросы — ответы", 5),
        )
    )
)


@dataclass(frozen=True, slots=True)
class ReportCard:
    course_id: int
    student_id: str
    lesson_id: int
    topic: str
    written: bool = False
    levels: tuple[int, ...] = tuple(c.steps // 2 for c in CRITERIA)

    def __post_init__(self):
        if not self.topic.strip():
            raise AttendanceError("Укажите тему доклада.")
        if len(self.levels) != len(CRITERIA) or any(
            not isinstance(level, int) or not 0 <= level < criterion.steps
            for level, criterion in zip(self.levels, CRITERIA, strict=True)
        ):
            raise AttendanceError("Карточка должна содержать 18 допустимых уровней.")


@dataclass(frozen=True, slots=True)
class WrittenWork:
    id: int
    course_id: int
    title: str
    maximum: float = 100
    weight: float = 1

    def __post_init__(self):
        if not self.title.strip() or not isfinite(self.maximum) or self.maximum <= 0:
            raise AttendanceError("Укажите название и положительный максимум работы.")
        if not isfinite(self.weight) or self.weight <= 0:
            raise AttendanceError("Вес работы должен быть положительным.")


@dataclass(frozen=True, slots=True)
class CalculationParameters:
    version: int = 1
    neutral: float = 7
    spread: float = 3
    report_weight: float = 1
    criterion_weights: tuple[float, ...] = (1.0,) * 18
    three_values: tuple[float, ...] = (-1.0, 0.0, 1.0)
    five_values: tuple[float, ...] = (-1.0, -0.5, 0.0, 0.5, 1.0)
    work_weights: tuple[tuple[int, float], ...] = ()
    final_offset: float = 0
    final_scale: float = 1

    def __post_init__(self):
        if self.version < 1 or not 0 <= self.neutral <= 10:
            raise AttendanceError("Неверная версия или нейтральный ориентир.")
        values = (
            self.neutral,
            self.spread,
            self.report_weight,
            self.final_offset,
            self.final_scale,
            *self.criterion_weights,
            *(w for _, w in self.work_weights),
        )
        if (
            any(not isfinite(v) for v in values)
            or min(
                self.spread,
                self.report_weight,
                self.final_scale,
                *self.criterion_weights,
                *(w for _, w in self.work_weights),
            )
            <= 0
            or len(self.criterion_weights) != 18
        ):
            raise AttendanceError("Коэффициенты должны быть конечными и положительными.")
        if len(dict(self.work_weights)) != len(self.work_weights):
            raise AttendanceError("Повторяется вес работы.")
        for steps, values in ((3, self.three_values), (5, self.five_values)):
            if (
                len(values) != steps
                or any(not isfinite(v) for v in values)
                or values[steps // 2] != 0
                or any(a > b for a, b in zip(values, values[1:], strict=False))
                or values[0] < -1
                or values[-1] > 1
            ):
                raise AttendanceError(
                    "Вклады уровней должны сохранять порядок и нейтральный центр."
                )


def report_grade(card: ReportCard, parameters: CalculationParameters) -> float:
    numerator = denominator = 0.0
    for criterion, level, weight in zip(
        CRITERIA, card.levels, parameters.criterion_weights, strict=True
    ):
        if criterion.written and not card.written:
            continue
        values = parameters.three_values if criterion.steps == 3 else parameters.five_values
        numerator += values[level] * weight
        denominator += weight
    return max(0.0, min(10.0, parameters.neutral + parameters.spread * numerator / denominator))


@dataclass(frozen=True, slots=True)
class GradeResult:
    student_id: str
    report: float | None
    final: float | None
    missing: tuple[str, ...]
    parameter_version: int

    @property
    def mark(self) -> int | None:
        return None if self.final is None else min(10, int(self.final + 0.5))


def calculate(
    student_id: str,
    works: tuple[WrittenWork, ...],
    scores: dict[int, float],
    card: ReportCard | None,
    report_enabled: bool,
    parameters: CalculationParameters,
) -> GradeResult:
    missing = []
    numerator = denominator = 0.0
    report = report_grade(card, parameters) if card else None
    if report_enabled:
        if report is None:
            missing.append("Доклад")
        else:
            numerator += report * parameters.report_weight
            denominator += parameters.report_weight
    weights = dict(parameters.work_weights)
    for work in works:
        score = scores.get(work.id)
        if score is None:
            missing.append(work.title)
            continue
        if not isfinite(score) or not 0 <= score <= work.maximum:
            raise AttendanceError("Баллы выходят за исходную шкалу работы.")
        weight = weights.get(work.id, work.weight)
        numerator += score / work.maximum * 10 * weight
        denominator += weight
    final = None
    if not missing and denominator:
        final = max(
            0.0,
            min(10.0, numerator / denominator * parameters.final_scale + parameters.final_offset),
        )
    return GradeResult(student_id, report, final, tuple(missing), parameters.version)


@dataclass(frozen=True, slots=True)
class Distribution:
    n: int
    incomplete: int
    mean: float | None
    deviation: float | None
    counts: tuple[int, ...]
    normality: str


def distribution(results: tuple[GradeResult, ...]) -> Distribution:
    values = [r.final for r in results if r.final is not None]
    n = len(values)
    counts = tuple(sum(r.mark == mark for r in results) for mark in range(11))
    mean = fmean(values) if n else None
    deviation = pstdev(values) if n else None
    diagnostic = "Недостаточно данных (нужно не менее 20 полных результатов)."
    if n >= 20 and deviation and mean is not None:
        skew = fmean(((v - mean) / deviation) ** 3 for v in values)
        kurtosis = fmean(((v - mean) / deviation) ** 4 for v in values)
        jb = n / 6 * (skew * skew + (kurtosis - 3) ** 2 / 4)
        diagnostic = (
            f"Приближённая диагностика Jarque–Bera: p≈{exp(-jb / 2):.3f}. "
            "Шкала ограничена; это не подтверждение нормальности, особенно после подбора."
        )
    elif n >= 20:
        diagnostic = "Все оценки одинаковы; диагностика нормальности неприменима."
    return Distribution(n, len(results) - n, mean, deviation, counts, diagnostic)


@dataclass(frozen=True, slots=True)
class FitBound:
    field: str
    minimum: float
    maximum: float
    locked: bool = False


def fit_parameters(
    current: CalculationParameters,
    evaluate,
    bounds: tuple[FitBound, ...],
    target_mean: float,
    target_deviation: float,
) -> CalculationParameters:
    """Deterministic bounded coordinate search against the common course function."""
    if not 0 <= target_mean <= 10 or not 0 <= target_deviation <= 5:
        raise AttendanceError("Целевые параметры выходят за диапазон шкалы.")
    allowed = {"neutral", "spread", "report_weight", "final_offset", "final_scale"}
    if len({b.field for b in bounds}) != len(bounds):
        raise AttendanceError("Параметр подбора указан несколько раз.")

    def parameter_value(parameters, field):
        if ":" not in field:
            if field not in allowed:
                raise AttendanceError("Неизвестный параметр подбора.")
            return getattr(parameters, field)
        kind, index_text = field.split(":", 1)
        try:
            index = int(index_text)
            if kind == "work":
                return dict(parameters.work_weights)[index]
            options = {
                "criterion": parameters.criterion_weights,
                "three": parameters.three_values,
                "five": parameters.five_values,
            }
            if index < 0 or (kind == "three" and index == 1) or (kind == "five" and index == 2):
                raise ValueError()
            return options[kind][index]
        except (ValueError, KeyError, IndexError) as exc:
            raise AttendanceError("Неизвестный параметр подбора.") from exc

    def change(parameters, field, value):
        if ":" not in field:
            return replace(parameters, **{field: value})
        kind, index_text = field.split(":", 1)
        index = int(index_text)
        names = {"criterion": "criterion_weights", "three": "three_values", "five": "five_values"}
        if kind == "work":
            works = dict(parameters.work_weights)
            works[index] = value
            return replace(parameters, work_weights=tuple(works.items()))
        values = list(getattr(parameters, names[kind]))
        values[index] = value
        return replace(parameters, **{names[kind]: tuple(values)})

    for bound in bounds:
        original = parameter_value(current, bound.field)
        if (
            not isfinite(original)
            or not isfinite(bound.minimum)
            or not isfinite(bound.maximum)
            or bound.minimum > bound.maximum
            or not bound.minimum <= original <= bound.maximum
        ):
            raise AttendanceError("Некорректные границы подбора.")

    def loss(parameters):
        stats = distribution(tuple(evaluate(parameters)))
        if stats.mean is None:
            raise AttendanceError("Нет полных результатов для подбора.")
        return (stats.mean - target_mean) ** 2 + ((stats.deviation or 0) - target_deviation) ** 2

    best, best_loss = current, loss(current)
    for _ in range(3):
        for bound in bounds:
            if bound.locked:
                continue
            for step in range(21):
                value = bound.minimum + (bound.maximum - bound.minimum) * step / 20
                try:
                    candidate = change(best, bound.field, value)
                except AttendanceError:
                    continue
                score = loss(candidate)
                if score < best_loss:
                    best, best_loss = candidate, score
    return best
