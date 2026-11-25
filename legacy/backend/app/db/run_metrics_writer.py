from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generation_run import RUN_SUCCESS_STATUS, GenerationRun
from core.urban_generator.domain import DataError
from core.urban_generator.metrics.score import CompositeScoreResult

RUN_EVALUATION_METRICS_KEY = "evaluation"
RUN_EVALUATION_SCHEMA_VERSION = "1"


class RunMetricsPersistenceError(DataError):
    """Base error for canonical run evaluation persistence."""


class RunMetricsImmutableError(RunMetricsPersistenceError):
    """Raised before mutating evaluation metrics of a successful run."""


@dataclass(frozen=True, slots=True)
class RunMetricsWriteResult:
    run_id: uuid.UUID
    metric_count: int
    composite_score: float


class SqlAlchemyRunMetricsWriter:
    """Persist a versioned score envelope into GenerationRun.metrics_json."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._session_factory = session_factory

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        result: CompositeScoreResult,
    ) -> RunMetricsWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(result, CompositeScoreResult):
            raise TypeError("result must be CompositeScoreResult")

        try:
            with self._session_factory() as session:
                with session.begin():
                    run = session.scalars(
                        select(GenerationRun)
                        .where(GenerationRun.id == run_id)
                        .with_for_update()
                    ).one_or_none()
                    if run is None:
                        raise RunMetricsPersistenceError(
                            f"generation run not found: {run_id}"
                        )
                    if run.status == RUN_SUCCESS_STATUS:
                        raise RunMetricsImmutableError(
                            "evaluation metrics of a successful generation run are immutable"
                        )

                    metrics_json = dict(run.metrics_json or {})
                    metrics_json[RUN_EVALUATION_METRICS_KEY] = (
                        composite_score_payload(result)
                    )
                    run.metrics_json = metrics_json

            return RunMetricsWriteResult(
                run_id=run_id,
                metric_count=len(result.metrics),
                composite_score=result.score,
            )
        except RunMetricsPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise RunMetricsPersistenceError(
                f"unable to persist evaluation metrics for run {run_id}"
            ) from exc


def composite_score_payload(result: CompositeScoreResult) -> dict[str, Any]:
    if not isinstance(result, CompositeScoreResult):
        raise TypeError("result must be CompositeScoreResult")
    return {
        "schema_version": RUN_EVALUATION_SCHEMA_VERSION,
        "composite_score": result.score,
        "score_config": {
            "id": result.score_config_id,
            "version": result.score_config_version,
        },
        "normalization_profile": {
            "id": result.normalization_profile_id,
            "version": result.normalization_profile_version,
        },
        "metrics": [
            {
                "metric_id": item.metric_id.value,
                "raw_value": item.raw_value,
                "normalized_value": item.normalized_value,
                "normalization_policy_version": item.normalization_policy_version,
                "configured_weight": item.configured_weight,
                "normalized_weight": item.normalized_weight,
                "contribution": item.contribution,
                "was_clamped": item.was_clamped,
                "was_missing": item.was_missing,
            }
            for item in result.metrics
        ],
    }
