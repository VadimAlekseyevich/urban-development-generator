from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from core.urban_generator.domain.benchmarking import (
    CANONICAL_METRIC_REGISTRY,
    MetricDirection,
    MetricScope,
    RawMetricId,
)
from core.urban_generator.metrics.score import (
    CompositeScoreError,
    CompositeScoreMetricResult,
    CompositeScoreResult,
)

RUN_EVALUATION_METRICS_KEY = "evaluation"
RUN_EVALUATION_SCHEMA_VERSION = "1"
DEFAULT_METRIC_RUN_LIMIT = 50
MAX_METRIC_RUN_LIMIT = 100


class MetricDashboardQueryError(ValueError):
    """Raised when a metrics-dashboard request violates its public contract."""


class MetricDashboardProjectNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        super().__init__(f"project {project_id} was not found")


class MetricDashboardRunNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.run_id = run_id
        super().__init__(
            f"generation run {run_id} was not found in project {project_id}"
        )


class MetricDashboardUnavailableError(RuntimeError):
    """Raised when a run exists but has no persisted evaluation envelope."""


class MetricDashboardDataError(RuntimeError):
    """Raised when persisted evaluation data violates the canonical contract."""


@dataclass(frozen=True, slots=True)
class MetricDashboardRunRecord:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    metrics_json: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class MetricRunSummary:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    composite_score: float
    metric_count: int
    score_config_id: str
    score_config_version: str
    normalization_profile_id: str
    normalization_profile_version: str
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class MetricRunListResult:
    project_id: uuid.UUID
    limit: int
    truncated: bool
    runs: tuple[MetricRunSummary, ...]


@dataclass(frozen=True, slots=True)
class MetricDashboardMetric:
    metric_id: RawMetricId
    unit: str
    scope: MetricScope
    direction: MetricDirection
    metric_version: str
    raw_value: float | None
    normalized_value: float | None
    normalization_policy_version: str
    configured_weight: float
    normalized_weight: float
    contribution: float
    was_clamped: bool
    was_missing: bool


@dataclass(frozen=True, slots=True)
class MetricDashboardResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    composite_score: float
    score_config_id: str
    score_config_version: str
    normalization_profile_id: str
    normalization_profile_version: str
    metrics: tuple[MetricDashboardMetric, ...]
    created_at: datetime
    finished_at: datetime | None


class MetricDashboardQueryRepository(Protocol):
    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        ...

    def list_metric_runs(
        self,
        *,
        project_id: uuid.UUID,
        limit: int,
    ) -> list[MetricDashboardRunRecord]:
        ...

    def get_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> MetricDashboardRunRecord | None:
        ...


class MetricDashboardQueryService:
    """Project persisted canonical evaluation envelopes into UI read models."""

    def __init__(self, repository: MetricDashboardQueryRepository) -> None:
        self._repository = repository

    def list_runs(
        self,
        *,
        project_id: uuid.UUID,
        limit: int = DEFAULT_METRIC_RUN_LIMIT,
    ) -> MetricRunListResult:
        _require_limit(limit)
        if not self._repository.project_exists(project_id=project_id):
            raise MetricDashboardProjectNotFoundError(project_id)

        records = self._repository.list_metric_runs(
            project_id=project_id,
            limit=limit + 1,
        )
        truncated = len(records) > limit
        return MetricRunListResult(
            project_id=project_id,
            limit=limit,
            truncated=truncated,
            runs=tuple(self._summary(record) for record in records[:limit]),
        )

    def get_dashboard(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> MetricDashboardResult:
        record = self._repository.get_run(
            project_id=project_id,
            run_id=run_id,
        )
        if record is None:
            raise MetricDashboardRunNotFoundError(project_id, run_id)

        result = _decode_evaluation(record)
        return MetricDashboardResult(
            project_id=record.project_id,
            run_id=record.id,
            status=record.status,
            mode=record.mode,
            seed=record.seed,
            working_srid=record.working_srid,
            composite_score=result.score,
            score_config_id=result.score_config_id,
            score_config_version=result.score_config_version,
            normalization_profile_id=result.normalization_profile_id,
            normalization_profile_version=result.normalization_profile_version,
            metrics=tuple(_dashboard_metric(item) for item in result.metrics),
            created_at=record.created_at,
            finished_at=record.finished_at,
        )

    def _summary(self, record: MetricDashboardRunRecord) -> MetricRunSummary:
        result = _decode_evaluation(record)
        return MetricRunSummary(
            id=record.id,
            project_id=record.project_id,
            status=record.status,
            mode=record.mode,
            seed=record.seed,
            working_srid=record.working_srid,
            composite_score=result.score,
            metric_count=len(result.metrics),
            score_config_id=result.score_config_id,
            score_config_version=result.score_config_version,
            normalization_profile_id=result.normalization_profile_id,
            normalization_profile_version=result.normalization_profile_version,
            created_at=record.created_at,
            finished_at=record.finished_at,
        )


def _decode_evaluation(record: MetricDashboardRunRecord) -> CompositeScoreResult:
    metrics_json = record.metrics_json
    if not isinstance(metrics_json, dict):
        raise MetricDashboardUnavailableError(
            f"run {record.id} has no persisted evaluation envelope"
        )
    if RUN_EVALUATION_METRICS_KEY not in metrics_json:
        raise MetricDashboardUnavailableError(
            f"run {record.id} has no persisted evaluation envelope"
        )

    evaluation = metrics_json[RUN_EVALUATION_METRICS_KEY]
    if not isinstance(evaluation, dict):
        raise MetricDashboardDataError(
            f"run {record.id} persisted evaluation must be an object"
        )
    if evaluation.get("schema_version") != RUN_EVALUATION_SCHEMA_VERSION:
        raise MetricDashboardDataError(
            f"run {record.id} has unsupported evaluation schema_version"
        )

    try:
        score_config = _require_dict(evaluation, "score_config")
        profile = _require_dict(evaluation, "normalization_profile")
        raw_metrics = evaluation.get("metrics")
        if not isinstance(raw_metrics, list) or not raw_metrics:
            raise TypeError("metrics must be a non-empty list")

        metrics = tuple(
            _decode_metric(raw_item, index=index)
            for index, raw_item in enumerate(raw_metrics)
        )
        return CompositeScoreResult(
            score=_require_number(evaluation, "composite_score"),
            score_config_id=_require_string(score_config, "id"),
            score_config_version=_require_string(score_config, "version"),
            normalization_profile_id=_require_string(profile, "id"),
            normalization_profile_version=_require_string(profile, "version"),
            metrics=metrics,
        )
    except (CompositeScoreError, TypeError, ValueError) as exc:
        raise MetricDashboardDataError(
            f"run {record.id} has malformed persisted evaluation data"
        ) from exc


def _decode_metric(
    raw_item: object,
    *,
    index: int,
) -> CompositeScoreMetricResult:
    if not isinstance(raw_item, dict):
        raise TypeError(f"metric[{index}] must be an object")
    metric_id = RawMetricId(_require_string(raw_item, "metric_id"))
    return CompositeScoreMetricResult(
        metric_id=metric_id,
        raw_value=_optional_number(raw_item, "raw_value"),
        normalized_value=_optional_number(raw_item, "normalized_value"),
        normalization_policy_version=_require_string(
            raw_item,
            "normalization_policy_version",
        ),
        configured_weight=_require_number(raw_item, "configured_weight"),
        normalized_weight=_require_number(raw_item, "normalized_weight"),
        contribution=_require_number(raw_item, "contribution"),
        was_clamped=_require_bool(raw_item, "was_clamped"),
        was_missing=_require_bool(raw_item, "was_missing"),
    )


def _dashboard_metric(
    item: CompositeScoreMetricResult,
) -> MetricDashboardMetric:
    definition = CANONICAL_METRIC_REGISTRY.get(item.metric_id)
    return MetricDashboardMetric(
        metric_id=item.metric_id,
        unit=definition.unit,
        scope=definition.scope,
        direction=definition.direction,
        metric_version=definition.version,
        raw_value=item.raw_value,
        normalized_value=item.normalized_value,
        normalization_policy_version=item.normalization_policy_version,
        configured_weight=item.configured_weight,
        normalized_weight=item.normalized_weight,
        contribution=item.contribution,
        was_clamped=item.was_clamped,
        was_missing=item.was_missing,
    )


def _require_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise MetricDashboardQueryError("run limit must be an integer")
    if not 1 <= limit <= MAX_METRIC_RUN_LIMIT:
        raise MetricDashboardQueryError(
            f"run limit must be within [1, {MAX_METRIC_RUN_LIMIT}]"
        )


def _require_dict(
    item: dict[str, Any],
    field_name: str,
) -> dict[str, Any]:
    value = item.get(field_name)
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be an object")
    return value


def _require_string(
    item: dict[str, Any],
    field_name: str,
) -> str:
    value = item.get(field_name)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field_name} must be a non-empty string")
    return value


def _require_number(
    item: dict[str, Any],
    field_name: str,
) -> float:
    value = item.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric")
    return float(value)


def _optional_number(
    item: dict[str, Any],
    field_name: str,
) -> float | None:
    value = item.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric or null")
    return float(value)


def _require_bool(
    item: dict[str, Any],
    field_name: str,
) -> bool:
    value = item.get(field_name)
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be boolean")
    return value
