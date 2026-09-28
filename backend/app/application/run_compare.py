"""Bounded, read-only S12 comparison of persisted S11 metric and validation records."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from backend.app.application.metric_dashboard import (
    MetricDashboardDataError,
    MetricDashboardRunRecord,
    MetricDashboardUnavailableError,
    decode_persisted_evaluation,
)
from backend.app.db.run_validation_writer import MAX_PERSISTED_VALIDATION_RESULTS
from core.urban_generator.domain import (
    ValidationReport,
    ValidationReportCodecError,
    deserialize_validation_report,
)
from core.urban_generator.domain.benchmarking import (
    CANONICAL_METRIC_REGISTRY,
    MetricDirection,
    MetricScope,
    RawMetricId,
)
from core.urban_generator.metrics.score import CompositeScoreResult

MIN_COMPARE_RUNS = 2
MAX_COMPARE_RUNS = 10


class RunCompareQueryError(ValueError):
    """The requested comparison is not well formed or has incompatible inputs."""


class RunCompareProjectNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID) -> None:
        super().__init__(f"project {project_id} was not found")


class RunCompareRunNotFoundError(LookupError):
    """A run is missing or outside the requested project."""


class RunCompareUnavailableError(RuntimeError):
    """Only immutable completed runs with persisted evaluation/validation are comparable."""


class RunCompareDataError(RuntimeError):
    """A persisted run has broken canonical metric or validation provenance."""


@dataclass(frozen=True, slots=True)
class CompareRunRecord(MetricDashboardRunRecord):
    validation_json: dict[str, Any] | None


class RunCompareRepository(Protocol):
    def project_exists(self, *, project_id: uuid.UUID) -> bool: ...

    def get_runs(
        self, *, project_id: uuid.UUID, run_ids: tuple[uuid.UUID, ...]
    ) -> list[CompareRunRecord]: ...


@dataclass(frozen=True, slots=True)
class CompareValidationSummary:
    violation_count: int
    hard_violation_count: int
    soft_violation_count: int
    spatial_violation_count: int


@dataclass(frozen=True, slots=True)
class CompareRun:
    run_id: uuid.UUID
    seed: int
    mode: str
    working_srid: int
    score_config_id: str
    score_config_version: str
    normalization_profile_id: str
    normalization_profile_version: str
    composite_score: float
    score_delta_from_baseline: float | None
    score_rank: int | None
    validation: CompareValidationSummary


@dataclass(frozen=True, slots=True)
class CompareMetricValue:
    run_id: uuid.UUID
    raw_value: float | None
    delta_from_baseline: float | None
    rank: int | None


@dataclass(frozen=True, slots=True)
class CompareMetric:
    metric_id: RawMetricId
    unit: str
    scope: MetricScope
    direction: MetricDirection
    definition_version: str
    values: tuple[CompareMetricValue, ...]


@dataclass(frozen=True, slots=True)
class RunCompareResult:
    project_id: uuid.UUID
    baseline_run_id: uuid.UUID
    run_ids: tuple[uuid.UUID, ...]
    scores_comparable: bool
    runs: tuple[CompareRun, ...]
    metrics: tuple[CompareMetric, ...]


class RunCompareService:
    """Use one bounded DB read, existing score codec and canonical validation codec."""

    def __init__(self, repository: RunCompareRepository) -> None:
        self._repository = repository

    def compare(
        self, *, project_id: uuid.UUID, run_ids: tuple[uuid.UUID, ...]
    ) -> RunCompareResult:
        if not isinstance(project_id, uuid.UUID):
            raise RunCompareQueryError("project_id must be UUID")
        if (
            not isinstance(run_ids, tuple)
            or not MIN_COMPARE_RUNS <= len(run_ids) <= MAX_COMPARE_RUNS
            or any(not isinstance(run_id, uuid.UUID) for run_id in run_ids)
            or len(set(run_ids)) != len(run_ids)
        ):
            raise RunCompareQueryError(
                f"run_ids must contain {MIN_COMPARE_RUNS}–{MAX_COMPARE_RUNS} unique UUIDs"
            )

        records = self._repository.get_runs(project_id=project_id, run_ids=run_ids)
        by_id = {record.id: record for record in records}
        if len(by_id) != len(run_ids):
            if not self._repository.project_exists(project_id=project_id):
                raise RunCompareProjectNotFoundError(project_id)
            raise RunCompareRunNotFoundError(
                "one or more requested runs are missing from the project"
            )
        ordered = tuple(by_id[run_id] for run_id in run_ids)
        if any(record.status != "succeeded" for record in ordered):
            raise RunCompareUnavailableError(
                "comparison requires immutable successful generation runs"
            )
        if len({record.working_srid for record in ordered}) != 1:
            raise RunCompareUnavailableError(
                "comparison requires the same metric working SRID"
            )

        scores: list[CompositeScoreResult] = []
        validations: list[CompareValidationSummary] = []
        for record in ordered:
            try:
                scores.append(decode_persisted_evaluation(record))
            except MetricDashboardUnavailableError as exc:
                raise RunCompareUnavailableError(
                    f"run {record.id} has no persisted evaluation"
                ) from exc
            except MetricDashboardDataError as exc:
                raise RunCompareDataError(
                    f"run {record.id} has invalid persisted evaluation"
                ) from exc
            if record.validation_json is None:
                raise RunCompareUnavailableError(
                    f"run {record.id} has no persisted validation report"
                )
            validations.append(_validation_summary(record))

        # A score ranking/delta is meaningful only under exactly the same
        # versioned scoring inputs, including weights and normalization policies.
        comparable = all(
            _score_contract(score) == _score_contract(scores[0])
            for score in scores[1:]
        )
        score_ranks = _ranks(
            tuple(score.score for score in scores), direction=MetricDirection.HIGHER_IS_BETTER
        ) if comparable else (None,) * len(scores)
        runs = tuple(
            CompareRun(
                run_id=record.id,
                seed=record.seed,
                mode=record.mode,
                working_srid=record.working_srid,
                score_config_id=score.score_config_id,
                score_config_version=score.score_config_version,
                normalization_profile_id=score.normalization_profile_id,
                normalization_profile_version=score.normalization_profile_version,
                composite_score=score.score,
                score_delta_from_baseline=(
                    score.score - scores[0].score if comparable else None
                ),
                score_rank=score_ranks[index],
                validation=validations[index],
            )
            for index, (record, score) in enumerate(zip(ordered, scores, strict=True))
        )

        score_metrics = [
            {item.metric_id: item.raw_value for item in score.metrics}
            for score in scores
        ]
        metrics: list[CompareMetric] = []
        for definition in CANONICAL_METRIC_REGISTRY.definitions:
            if not any(definition.metric_id in row for row in score_metrics):
                continue
            raw_values = tuple(
                row.get(definition.metric_id) for row in score_metrics
            )
            ranks = _ranks(raw_values, direction=definition.direction)
            baseline = raw_values[0]
            metrics.append(
                CompareMetric(
                    metric_id=definition.metric_id,
                    unit=definition.unit,
                    scope=definition.scope,
                    direction=definition.direction,
                    definition_version=definition.version,
                    values=tuple(
                        CompareMetricValue(
                            run_id=run_id,
                            raw_value=value,
                            delta_from_baseline=(
                                value - baseline
                                if value is not None and baseline is not None
                                else None
                            ),
                            rank=ranks[index],
                        )
                        for index, (run_id, value) in enumerate(
                            zip(run_ids, raw_values, strict=True)
                        )
                    ),
                )
            )
        return RunCompareResult(
            project_id=project_id,
            baseline_run_id=run_ids[0],
            run_ids=run_ids,
            scores_comparable=comparable,
            runs=runs,
            metrics=tuple(metrics),
        )


def _validation_summary(record: CompareRunRecord) -> CompareValidationSummary:
    try:
        encoded = json.dumps(
            record.validation_json,
            allow_nan=False,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        report = deserialize_validation_report(encoded)
    except (TypeError, ValueError, ValidationReportCodecError) as exc:
        raise RunCompareDataError(
            f"run {record.id} has invalid persisted validation"
        ) from exc
    if (
        not isinstance(report, ValidationReport)
        or len(report.results) > MAX_PERSISTED_VALIDATION_RESULTS
    ):
        raise RunCompareDataError(f"run {record.id} has oversized validation")
    for item in report.results:
        if (
            item.problem_geometry is not None
            and item.problem_geometry.working_srid != record.working_srid
        ):
            raise RunCompareDataError(f"run {record.id} validation SRID disagrees with run")
    failures = report.failures
    return CompareValidationSummary(
        violation_count=len(failures),
        hard_violation_count=len(report.hard_failures),
        soft_violation_count=len(report.soft_violations),
        spatial_violation_count=sum(
            item.problem_geometry is not None for item in failures
        ),
    )


def _score_contract(score: CompositeScoreResult) -> tuple[object, ...]:
    return (
        score.score_config_id,
        score.score_config_version,
        score.normalization_profile_id,
        score.normalization_profile_version,
        tuple(sorted(
            (
                item.metric_id.value,
                item.normalization_policy_version,
                item.configured_weight,
                item.normalized_weight,
            )
            for item in score.metrics
        )),
    )


def _ranks(
    values: tuple[float | None, ...], *, direction: MetricDirection
) -> tuple[int | None, ...]:
    if direction not in {
        MetricDirection.HIGHER_IS_BETTER,
        MetricDirection.LOWER_IS_BETTER,
    }:
        return (None,) * len(values)
    available = [
        (index, value) for index, value in enumerate(values) if value is not None
    ]
    available.sort(
        key=lambda item: (
            -item[1] if direction is MetricDirection.HIGHER_IS_BETTER else item[1],
            item[0],
        )
    )
    ranks: list[int | None] = [None] * len(values)
    previous: float | None = None
    rank = 0
    for position, (index, value) in enumerate(available, start=1):
        if previous is None or value != previous:
            rank = position
        ranks[index] = rank
        previous = value
    return tuple(ranks)
