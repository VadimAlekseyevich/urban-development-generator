from __future__ import annotations

import math
from dataclasses import dataclass

from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
    ValidationReport,
)


class ConstraintMetricAdapterError(ValueError):
    """Raised when canonical validation data cannot be projected into S11 metrics."""


CONSTRAINT_RAW_METRIC_IDS = tuple(
    definition.metric_id
    for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
        source=MetricSource.CONSTRAINTS
    )
)


@dataclass(frozen=True, slots=True)
class ConstraintRawMetricValue:
    """One canonical S11-T09 scalar constraint metric."""

    metric_id: RawMetricId
    scalar_value: float

    def __post_init__(self) -> None:
        if self.metric_id not in CONSTRAINT_RAW_METRIC_IDS:
            raise ConstraintMetricAdapterError(
                f"not a constraint RawMetricId: {self.metric_id!r}"
            )
        scalar = _require_non_negative_finite(
            "constraint scalar metric",
            self.scalar_value,
        )
        object.__setattr__(self, "scalar_value", scalar)


@dataclass(frozen=True, slots=True)
class ConstraintMetricAdapterDiagnostics:
    result_count: int
    failure_count: int
    hard_failure_count: int
    soft_violation_count: int
    failure_with_problem_geometry_count: int
    polygonal_problem_geometry_count: int
    non_polygonal_problem_geometry_count: int
    invalid_problem_geometry_count: int
    missing_problem_geometry_failure_count: int
    soft_violation_with_penalty_count: int
    missing_soft_penalty_count: int
    working_srid: int | None

    def __post_init__(self) -> None:
        for field_name in (
            "result_count",
            "failure_count",
            "hard_failure_count",
            "soft_violation_count",
            "failure_with_problem_geometry_count",
            "polygonal_problem_geometry_count",
            "non_polygonal_problem_geometry_count",
            "invalid_problem_geometry_count",
            "missing_problem_geometry_failure_count",
            "soft_violation_with_penalty_count",
            "missing_soft_penalty_count",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ConstraintMetricAdapterError(
                    f"{field_name} must be a non-negative integer"
                )

        if self.failure_count > self.result_count:
            raise ConstraintMetricAdapterError(
                "failure_count cannot exceed result_count"
            )
        if self.hard_failure_count + self.soft_violation_count > self.failure_count:
            raise ConstraintMetricAdapterError(
                "hard and soft violation counts cannot exceed failure_count"
            )
        if (
            self.failure_with_problem_geometry_count
            + self.missing_problem_geometry_failure_count
            != self.failure_count
        ):
            raise ConstraintMetricAdapterError(
                "problem-geometry diagnostics must account for every failure"
            )
        if (
            self.polygonal_problem_geometry_count
            + self.non_polygonal_problem_geometry_count
            != self.failure_with_problem_geometry_count
        ):
            raise ConstraintMetricAdapterError(
                "problem geometry kind counts must match geometry-bearing failures"
            )
        if (
            self.soft_violation_with_penalty_count
            + self.missing_soft_penalty_count
            != self.soft_violation_count
        ):
            raise ConstraintMetricAdapterError(
                "soft-penalty diagnostics must account for every soft violation"
            )
        if self.working_srid is not None and (
            isinstance(self.working_srid, bool)
            or not isinstance(self.working_srid, int)
            or self.working_srid <= 0
        ):
            raise ConstraintMetricAdapterError(
                "working_srid must be a positive integer or None"
            )


@dataclass(frozen=True, slots=True)
class ConstraintRawMetricsResult:
    raw_metrics: tuple[ConstraintRawMetricValue, ...]
    diagnostics: ConstraintMetricAdapterDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.raw_metrics, tuple):
            raise ConstraintMetricAdapterError(
                "raw_metrics must be an immutable tuple"
            )
        if any(
            not isinstance(item, ConstraintRawMetricValue)
            for item in self.raw_metrics
        ):
            raise ConstraintMetricAdapterError(
                "raw_metrics must contain ConstraintRawMetricValue values"
            )
        actual_ids = tuple(item.metric_id for item in self.raw_metrics)
        if actual_ids != CONSTRAINT_RAW_METRIC_IDS:
            raise ConstraintMetricAdapterError(
                "raw_metrics must contain every canonical constraint metric "
                "in registry order"
            )
        if not isinstance(self.diagnostics, ConstraintMetricAdapterDiagnostics):
            raise ConstraintMetricAdapterError(
                "diagnostics must be ConstraintMetricAdapterDiagnostics"
            )

    def require(self, metric_id: RawMetricId) -> ConstraintRawMetricValue:
        if metric_id not in CONSTRAINT_RAW_METRIC_IDS:
            raise ConstraintMetricAdapterError(
                f"not a constraint RawMetricId: {metric_id!r}"
            )
        return self.raw_metrics[CONSTRAINT_RAW_METRIC_IDS.index(metric_id)]


class ConstraintMetricAdapter:
    """Project one canonical ValidationReport into S11 raw constraint metrics."""

    version = "1"

    def adapt(self, report: ValidationReport) -> ConstraintRawMetricsResult:
        if not isinstance(report, ValidationReport):
            raise ConstraintMetricAdapterError(
                "report must be a ValidationReport"
            )

        area, geometry_diagnostics = _affected_area(report)

        soft_with_penalty = tuple(
            result
            for result in report.soft_violations
            if result.soft_penalty is not None
        )
        weighted_soft_penalty = math.fsum(
            result.soft_penalty.weighted_penalty
            for result in soft_with_penalty
            if result.soft_penalty is not None
        )

        values_by_id = {
            RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT: (
                ConstraintRawMetricValue(
                    metric_id=RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
                    scalar_value=float(len(report.hard_failures)),
                )
            ),
            RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2: (
                ConstraintRawMetricValue(
                    metric_id=RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2,
                    scalar_value=area,
                )
            ),
            RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY: (
                ConstraintRawMetricValue(
                    metric_id=RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
                    scalar_value=weighted_soft_penalty,
                )
            ),
        }

        return ConstraintRawMetricsResult(
            raw_metrics=tuple(
                values_by_id[metric_id]
                for metric_id in CONSTRAINT_RAW_METRIC_IDS
            ),
            diagnostics=ConstraintMetricAdapterDiagnostics(
                result_count=len(report.results),
                failure_count=len(report.failures),
                hard_failure_count=len(report.hard_failures),
                soft_violation_count=len(report.soft_violations),
                failure_with_problem_geometry_count=(
                    geometry_diagnostics.with_geometry
                ),
                polygonal_problem_geometry_count=(
                    geometry_diagnostics.polygonal
                ),
                non_polygonal_problem_geometry_count=(
                    geometry_diagnostics.non_polygonal
                ),
                invalid_problem_geometry_count=(
                    geometry_diagnostics.invalid
                ),
                missing_problem_geometry_failure_count=(
                    len(report.failures) - geometry_diagnostics.with_geometry
                ),
                soft_violation_with_penalty_count=len(soft_with_penalty),
                missing_soft_penalty_count=(
                    len(report.soft_violations) - len(soft_with_penalty)
                ),
                working_srid=geometry_diagnostics.working_srid,
            ),
        )


@dataclass(frozen=True, slots=True)
class _GeometryDiagnostics:
    with_geometry: int
    polygonal: int
    non_polygonal: int
    invalid: int
    working_srid: int | None


def _affected_area(
    report: ValidationReport,
) -> tuple[float, _GeometryDiagnostics]:
    polygonal_parts: list[BaseGeometry] = []
    working_srid: int | None = None
    with_geometry = 0
    polygonal = 0
    non_polygonal = 0
    invalid = 0

    for result in report.failures:
        detail = result.problem_geometry
        if detail is None:
            continue
        with_geometry += 1

        if working_srid is None:
            working_srid = detail.working_srid
        elif detail.working_srid != working_srid:
            raise ConstraintMetricAdapterError(
                "failed constraint problem geometries must use one working_srid"
            )

        geometry = detail.geometry
        if not geometry.is_valid:
            invalid += 1
            geometry = make_valid(geometry)

        parts = _polygonal_parts(geometry)
        if parts:
            polygonal += 1
            polygonal_parts.extend(parts)
        else:
            non_polygonal += 1

    if not polygonal_parts:
        area = 0.0
    else:
        area = float(unary_union(tuple(polygonal_parts)).area)
        _require_non_negative_finite("constraint affected area", area)

    return area, _GeometryDiagnostics(
        with_geometry=with_geometry,
        polygonal=polygonal,
        non_polygonal=non_polygonal,
        invalid=invalid,
        working_srid=working_srid,
    )


def _polygonal_parts(geometry: BaseGeometry) -> tuple[BaseGeometry, ...]:
    if isinstance(geometry, Polygon):
        return (geometry,)
    if isinstance(geometry, MultiPolygon):
        return tuple(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        parts: list[BaseGeometry] = []
        for item in geometry.geoms:
            parts.extend(_polygonal_parts(item))
        return tuple(parts)
    return ()


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConstraintMetricAdapterError(
            f"{field_name} must be a finite non-negative number"
        )
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ConstraintMetricAdapterError(
            f"{field_name} must be a finite non-negative number"
        )
    return numeric
