from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.run_metrics_writer import (
    RUN_EVALUATION_METRICS_KEY,
    RUN_EVALUATION_SCHEMA_VERSION,
)
from backend.app.db.session import SessionLocal
from backend.app.models.generation_run import GenerationRun
from core.urban_generator.domain import DataError
from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.score import (
    CompositeScoreConfig,
    CompositeScoreMetricWeight,
)
from core.urban_generator.metrics.sensitivity import (
    ScoreSensitivityMetricSnapshot,
    ScoreSensitivityRunSnapshot,
)


class RunScoreSensitivityPersistenceError(DataError):
    """Raised when persisted run evaluations cannot form a sensitivity dataset."""


@dataclass(frozen=True, slots=True)
class RunScoreSensitivityDataset:
    baseline_config: CompositeScoreConfig
    runs: tuple[ScoreSensitivityRunSnapshot, ...]


class SqlAlchemyRunScoreSensitivityReader:
    """Load bounded persisted evaluation snapshots without touching GIS outputs."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_runs: int = 100,
    ) -> None:
        if (
            not isinstance(max_runs, int)
            or isinstance(max_runs, bool)
            or max_runs < 1
        ):
            raise ValueError("max_runs must be a positive integer")
        self._session_factory = session_factory
        self._max_runs = max_runs

    def load(
        self,
        *,
        run_ids: tuple[uuid.UUID, ...],
    ) -> RunScoreSensitivityDataset:
        if not isinstance(run_ids, tuple) or not run_ids:
            raise RunScoreSensitivityPersistenceError(
                "run_ids must be a non-empty tuple"
            )
        if any(
            not isinstance(run_id, uuid.UUID) for run_id in run_ids
        ):
            raise RunScoreSensitivityPersistenceError(
                "run_ids must contain only UUID values"
            )
        if len(run_ids) != len(set(run_ids)):
            raise RunScoreSensitivityPersistenceError(
                "run_ids must not contain duplicates"
            )
        if len(run_ids) > self._max_runs:
            raise RunScoreSensitivityPersistenceError(
                f"run sensitivity request exceeds max_runs={self._max_runs}"
            )

        try:
            with self._session_factory() as session:
                rows = session.scalars(
                    select(GenerationRun)
                    .where(GenerationRun.id.in_(run_ids))
                ).all()
        except SQLAlchemyError as exc:
            raise RunScoreSensitivityPersistenceError(
                "unable to load persisted run evaluations"
            ) from exc

        by_id = {row.id: row for row in rows}
        missing = tuple(run_id for run_id in run_ids if run_id not in by_id)
        if missing:
            raise RunScoreSensitivityPersistenceError(
                "generation run not found: "
                + ", ".join(str(run_id) for run_id in missing)
            )

        parsed = tuple(
            _parse_evaluation(
                run_ref=str(run_id),
                metrics_json=by_id[run_id].metrics_json,
            )
            for run_id in run_ids
        )
        baseline = parsed[0][0]
        runs = tuple(item[1] for item in parsed)
        for config, _run in parsed[1:]:
            if (
                config.config_id != baseline.config_id
                or config.version != baseline.version
                or config.weights != baseline.weights
            ):
                raise RunScoreSensitivityPersistenceError(
                    "all persisted runs must use the same baseline score config"
                )

        return RunScoreSensitivityDataset(
            baseline_config=baseline,
            runs=runs,
        )


def _parse_evaluation(
    *,
    run_ref: str,
    metrics_json: dict[str, Any] | None,
) -> tuple[CompositeScoreConfig, ScoreSensitivityRunSnapshot]:
    if not isinstance(metrics_json, dict):
        raise RunScoreSensitivityPersistenceError(
            f"run {run_ref} has no persisted metrics"
        )
    evaluation = metrics_json.get(RUN_EVALUATION_METRICS_KEY)
    if not isinstance(evaluation, dict):
        raise RunScoreSensitivityPersistenceError(
            f"run {run_ref} has no persisted evaluation envelope"
        )
    if evaluation.get("schema_version") != RUN_EVALUATION_SCHEMA_VERSION:
        raise RunScoreSensitivityPersistenceError(
            f"run {run_ref} has unsupported evaluation schema_version"
        )

    score_config = _require_dict(
        evaluation.get("score_config"),
        field_name=f"run {run_ref} score_config",
    )
    profile = _require_dict(
        evaluation.get("normalization_profile"),
        field_name=f"run {run_ref} normalization_profile",
    )
    metric_rows = evaluation.get("metrics")
    if not isinstance(metric_rows, list) or not metric_rows:
        raise RunScoreSensitivityPersistenceError(
            f"run {run_ref} evaluation metrics must be a non-empty list"
        )

    weights: list[CompositeScoreMetricWeight] = []
    metrics: list[ScoreSensitivityMetricSnapshot] = []
    for index, raw_item in enumerate(metric_rows):
        item = _require_dict(
            raw_item,
            field_name=f"run {run_ref} metric[{index}]",
        )
        try:
            metric_id = RawMetricId(_require_str(item, "metric_id"))
            weight = _require_number(item, "configured_weight")
            raw_value = _optional_number(item, "raw_value")
            normalized_value = _optional_number(
                item,
                "normalized_value",
            )
            policy_version = _require_str(
                item,
                "normalization_policy_version",
            )
            was_clamped = _require_bool(item, "was_clamped")
            was_missing = _require_bool(item, "was_missing")
        except (TypeError, ValueError) as exc:
            raise RunScoreSensitivityPersistenceError(
                f"run {run_ref} has malformed persisted metric[{index}]"
            ) from exc

        weights.append(
            CompositeScoreMetricWeight(
                metric_id=metric_id,
                weight=weight,
            )
        )
        metrics.append(
            ScoreSensitivityMetricSnapshot(
                metric_id=metric_id,
                raw_value=raw_value,
                normalized_value=normalized_value,
                normalization_policy_version=policy_version,
                was_clamped=was_clamped,
                was_missing=was_missing,
            )
        )

    weights.sort(key=lambda item: item.metric_id.value)
    metrics.sort(key=lambda item: item.metric_id.value)

    try:
        config = CompositeScoreConfig(
            config_id=_require_str(score_config, "id"),
            version=_require_str(score_config, "version"),
            weights=tuple(weights),
        )
        run = ScoreSensitivityRunSnapshot(
            run_ref=run_ref,
            normalization_profile_id=_require_str(profile, "id"),
            normalization_profile_version=_require_str(
                profile,
                "version",
            ),
            metrics=tuple(metrics),
        )
    except (TypeError, ValueError) as exc:
        raise RunScoreSensitivityPersistenceError(
            f"run {run_ref} has malformed persisted evaluation metadata"
        ) from exc

    return config, run


def _require_dict(
    value: object,
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RunScoreSensitivityPersistenceError(
            f"{field_name} must be an object"
        )
    return value


def _require_str(
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
