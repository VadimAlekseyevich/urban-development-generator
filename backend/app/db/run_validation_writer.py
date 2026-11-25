from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generation_run import RUN_SUCCESS_STATUS, GenerationRun
from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    DataError,
    MetricValueKind,
    ValidationReport,
    serialize_validation_report,
)
from core.urban_generator.metrics import CompositeScoreRawMetric

MAX_PERSISTED_VALIDATION_RESULTS = 10_000


class RunValidationPersistenceError(DataError):
    """Base error for canonical run validation persistence."""


class RunValidationImmutableError(RunValidationPersistenceError):
    """Raised before mutating validation of a successful run."""


@dataclass(frozen=True, slots=True)
class RunValidationWriteResult:
    run_id: uuid.UUID
    result_count: int
    violation_count: int
    spatial_violation_count: int


class SqlAlchemyRunValidationWriter:
    """Persist one canonical ValidationReport on a GenerationRun."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_results: int = MAX_PERSISTED_VALIDATION_RESULTS,
    ) -> None:
        if (
            not isinstance(max_results, int)
            or isinstance(max_results, bool)
            or max_results < 1
        ):
            raise ValueError("max_results must be a positive integer")
        self._session_factory = session_factory
        self._max_results = max_results

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        report: ValidationReport,
        scalar_metrics: tuple[CompositeScoreRawMetric, ...] | None = None,
    ) -> RunValidationWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(report, ValidationReport):
            raise TypeError("report must be ValidationReport")
        if len(report.results) > self._max_results:
            raise RunValidationPersistenceError(
                "validation report exceeds configured result bound: "
                f"{len(report.results)} > {self._max_results}"
            )

        payload = _validation_report_payload(report)
        raw_payload = None if scalar_metrics is None else scalar_metrics_payload(scalar_metrics)
        try:
            with self._session_factory() as session:
                with session.begin():
                    run = session.scalars(
                        select(GenerationRun)
                        .where(GenerationRun.id == run_id)
                        .with_for_update()
                    ).one_or_none()
                    if run is None:
                        raise RunValidationPersistenceError(
                            f"generation run not found: {run_id}"
                        )
                    if run.status == RUN_SUCCESS_STATUS:
                        raise RunValidationImmutableError(
                            "validation of a successful generation run is immutable"
                        )
                    for result in report.results:
                        problem = result.problem_geometry
                        if (
                            problem is not None
                            and problem.working_srid != run.working_srid
                        ):
                            raise RunValidationPersistenceError(
                                "validation problem geometry working SRID must match "
                                f"run working_srid={run.working_srid}"
                            )
                    run.validation_json = payload
                    if raw_payload is not None:
                        metrics_json = dict(run.metrics_json or {})
                        metrics_json["raw_scalars"] = raw_payload
                        run.metrics_json = metrics_json
        except RunValidationPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise RunValidationPersistenceError(
                f"unable to persist validation report for run {run_id}"
            ) from exc

        failures = report.failures
        return RunValidationWriteResult(
            run_id=run_id,
            result_count=len(report.results),
            violation_count=len(failures),
            spatial_violation_count=sum(
                result.problem_geometry is not None for result in failures
            ),
        )


def _validation_report_payload(report: ValidationReport) -> dict[str, Any]:
    decoded: object = json.loads(serialize_validation_report(report))
    if not isinstance(decoded, dict):
        raise RunValidationPersistenceError(
            "canonical validation serializer returned a non-object payload"
        )
    return cast(dict[str, Any], decoded)


def scalar_metrics_payload(metrics: tuple[CompositeScoreRawMetric, ...]) -> dict[str, Any]:
    """Persist canonical scalars independently of score weights; null is unavailable."""
    if not isinstance(metrics, tuple) or not metrics:
        raise RunValidationPersistenceError("raw scalar metrics must be a non-empty tuple")
    values: dict[str, float | None] = {}
    for metric in metrics:
        if not isinstance(metric, CompositeScoreRawMetric):
            raise RunValidationPersistenceError("invalid raw scalar metric")
        if CANONICAL_METRIC_REGISTRY.get(metric.metric_id).value_kind is not MetricValueKind.SCALAR:
            raise RunValidationPersistenceError("distribution cannot be persisted as a scalar")
        if metric.metric_id.value in values:
            raise RunValidationPersistenceError("duplicate raw scalar metric")
        values[metric.metric_id.value] = metric.raw_value
    return {"schema_version": "1", "values": values}
