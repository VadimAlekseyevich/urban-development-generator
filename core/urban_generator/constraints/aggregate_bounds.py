from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.constraints.engine import ConstraintRegistration
from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.domain.constraints import (
    ConstraintContractError,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    validate_constraint_metadata,
)
from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot
from core.urban_generator.stages.catalog import FINAL_VALIDATION_STAGE


class AggregateBoundError(ConstraintContractError):
    """Raised when aggregate final-validation bound inputs violate the contract."""


CANONICAL_AGGREGATE_BOUND_METRIC_IDS = (
    RawMetricId.BUILDINGS_COVERAGE_RATIO,
    RawMetricId.BUILDINGS_FAR,
    RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
    RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
)


@dataclass(frozen=True, slots=True)
class AggregateMetricValue:
    """One authoritative aggregate value keyed by the canonical RawMetricId vocabulary."""

    metric_id: RawMetricId
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise AggregateBoundError("aggregate metric_id must be a RawMetricId value")
        value = _require_finite("aggregate metric value", self.value)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class AggregateConstraintSubject:
    """Immutable aggregate metrics consumed by final-validation constraints."""

    values: tuple[AggregateMetricValue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple):
            raise AggregateBoundError("aggregate metric values must be an immutable tuple")
        if any(not isinstance(item, AggregateMetricValue) for item in self.values):
            raise AggregateBoundError(
                "aggregate metric values must contain only AggregateMetricValue values"
            )
        metric_ids = tuple(item.metric_id for item in self.values)
        if len(metric_ids) != len(set(metric_ids)):
            raise AggregateBoundError(
                "aggregate metric values must not contain duplicate metric IDs"
            )

    def value_for(self, metric_id: RawMetricId) -> float:
        if not isinstance(metric_id, RawMetricId):
            raise AggregateBoundError(
                "aggregate lookup metric_id must be a RawMetricId value"
            )
        for item in self.values:
            if item.metric_id is metric_id:
                return item.value
        raise AggregateBoundError(
            f"aggregate metric value is missing: {metric_id.value}"
        )


@dataclass(frozen=True, slots=True)
class AggregateMetricBound:
    """Inclusive HARD lower/upper bound for one canonical aggregate metric."""

    metric_id: RawMetricId
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        if self.metric_id not in CANONICAL_AGGREGATE_BOUND_METRIC_IDS:
            raise AggregateBoundError(
                "aggregate bound metric_id must be one of the canonical "
                "coverage/FAR/density/capacity metrics"
            )
        if self.minimum is None and self.maximum is None:
            raise AggregateBoundError(
                "aggregate bound requires minimum and/or maximum"
            )

        minimum = (
            None
            if self.minimum is None
            else _require_finite("aggregate bound minimum", self.minimum)
        )
        maximum = (
            None
            if self.maximum is None
            else _require_finite("aggregate bound maximum", self.maximum)
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise AggregateBoundError(
                "aggregate bound minimum must not exceed maximum"
            )
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    @property
    def code(self) -> str:
        return f"aggregate.{self.metric_id.value}"


class AggregateMetricBoundConstraint:
    """HARD final-validation constraint over one authoritative aggregate metric."""

    severity = ConstraintSeverity.HARD
    scope = ConstraintScope.TERRITORY

    def __init__(self, bound: AggregateMetricBound) -> None:
        if not isinstance(bound, AggregateMetricBound):
            raise AggregateBoundError("bound must be an AggregateMetricBound")
        self.bound = bound
        self.code = bound.code
        validate_constraint_metadata(self.code, self.severity, self.scope)

    def evaluate(
        self,
        *,
        subject: AggregateConstraintSubject,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        if not isinstance(subject, AggregateConstraintSubject):
            raise AggregateBoundError(
                "subject must be an AggregateConstraintSubject"
            )
        if snapshot.settings.working_srid != context.working_srid:
            raise AggregateBoundError(
                "snapshot working_srid must match run context working_srid"
            )

        value = subject.value_for(self.bound.metric_id)
        if self.bound.minimum is not None and value < self.bound.minimum:
            return ConstraintResult(
                code=self.code,
                severity=self.severity,
                scope=self.scope,
                passed=False,
                message=(
                    f"{self.bound.metric_id.value}={value:.12g} is below "
                    f"minimum {self.bound.minimum:.12g}"
                ),
            )
        if self.bound.maximum is not None and value > self.bound.maximum:
            return ConstraintResult(
                code=self.code,
                severity=self.severity,
                scope=self.scope,
                passed=False,
                message=(
                    f"{self.bound.metric_id.value}={value:.12g} exceeds "
                    f"maximum {self.bound.maximum:.12g}"
                ),
            )
        return ConstraintResult(
            code=self.code,
            severity=self.severity,
            scope=self.scope,
            passed=True,
            message=(
                f"{self.bound.metric_id.value}={value:.12g} satisfies "
                "the configured aggregate bound"
            ),
        )


def build_aggregate_bound_registrations(
    bounds: tuple[AggregateMetricBound, ...],
) -> tuple[ConstraintRegistration, ...]:
    """Bind exactly the S11-T02 aggregate rules to the canonical final-validation stage."""

    if not isinstance(bounds, tuple):
        raise AggregateBoundError("aggregate bounds must be an immutable tuple")
    if any(not isinstance(bound, AggregateMetricBound) for bound in bounds):
        raise AggregateBoundError(
            "aggregate bounds must contain only AggregateMetricBound values"
        )

    metric_ids = tuple(bound.metric_id for bound in bounds)
    if len(metric_ids) != len(set(metric_ids)):
        raise AggregateBoundError(
            "aggregate bounds must not contain duplicate metric IDs"
        )
    if set(metric_ids) != set(CANONICAL_AGGREGATE_BOUND_METRIC_IDS):
        raise AggregateBoundError(
            "aggregate bounds must configure exactly coverage, FAR, density, "
            "and capacity utilization"
        )

    return tuple(
        ConstraintRegistration(
            stage=FINAL_VALIDATION_STAGE,
            constraint=AggregateMetricBoundConstraint(bound),
        )
        for bound in sorted(bounds, key=lambda item: item.metric_id.value)
    )


def _require_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AggregateBoundError(f"{field_name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise AggregateBoundError(f"{field_name} must be a finite number")
    return numeric
