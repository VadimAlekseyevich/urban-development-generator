from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.constraints.aggregate_bounds import (
    CANONICAL_AGGREGATE_BOUND_METRIC_IDS,
    AggregateBoundError,
    AggregateConstraintSubject,
)
from core.urban_generator.constraints.engine import ConstraintRegistration
from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.domain.constraints import (
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    SoftPenaltyMetadata,
    validate_constraint_metadata,
)
from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot
from core.urban_generator.stages.catalog import FINAL_VALIDATION_STAGE


class SoftPenaltyRuleError(AggregateBoundError):
    """Raised when a soft aggregate preference violates the S11-T03 contract."""


@dataclass(frozen=True, slots=True)
class SoftAggregatePreference:
    """Inclusive preferred range with a positive contribution weight."""

    metric_id: RawMetricId
    weight: float
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        if self.metric_id not in CANONICAL_AGGREGATE_BOUND_METRIC_IDS:
            raise SoftPenaltyRuleError(
                "soft preference metric_id must reuse a canonical aggregate metric"
            )
        weight = _require_finite("soft preference weight", self.weight)
        if weight <= 0.0:
            raise SoftPenaltyRuleError("soft preference weight must be positive")

        if self.minimum is None and self.maximum is None:
            raise SoftPenaltyRuleError(
                "soft preference requires minimum and/or maximum"
            )
        minimum = (
            None
            if self.minimum is None
            else _require_finite("soft preference minimum", self.minimum)
        )
        maximum = (
            None
            if self.maximum is None
            else _require_finite("soft preference maximum", self.maximum)
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise SoftPenaltyRuleError(
                "soft preference minimum must not exceed maximum"
            )

        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    @property
    def code(self) -> str:
        return f"preference.{self.metric_id.value}"


class SoftAggregatePreferenceConstraint:
    """SOFT aggregate rule whose violation contributes penalty but never hard-invalidates."""

    severity = ConstraintSeverity.SOFT
    scope = ConstraintScope.TERRITORY

    def __init__(self, preference: SoftAggregatePreference) -> None:
        if not isinstance(preference, SoftAggregatePreference):
            raise SoftPenaltyRuleError(
                "preference must be a SoftAggregatePreference"
            )
        self.preference = preference
        self.code = preference.code
        validate_constraint_metadata(self.code, self.severity, self.scope)

    def evaluate(
        self,
        *,
        subject: AggregateConstraintSubject,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        if not isinstance(subject, AggregateConstraintSubject):
            raise SoftPenaltyRuleError(
                "subject must be an AggregateConstraintSubject"
            )
        if snapshot.settings.working_srid != context.working_srid:
            raise SoftPenaltyRuleError(
                "snapshot working_srid must match run context working_srid"
            )

        value = subject.value_for(self.preference.metric_id)
        failure_message = _failure_message(
            metric_id=self.preference.metric_id,
            value=value,
            minimum=self.preference.minimum,
            maximum=self.preference.maximum,
        )
        if failure_message is None:
            return ConstraintResult(
                code=self.code,
                severity=self.severity,
                scope=self.scope,
                passed=True,
                message=(
                    f"{self.preference.metric_id.value}={value:.12g} satisfies "
                    "the configured soft preference"
                ),
                soft_penalty=SoftPenaltyMetadata(
                    raw_penalty=0.0,
                    weight=self.preference.weight,
                ),
            )

        return ConstraintResult(
            code=self.code,
            severity=self.severity,
            scope=self.scope,
            passed=False,
            message=failure_message,
            soft_penalty=SoftPenaltyMetadata(
                raw_penalty=1.0,
                weight=self.preference.weight,
            ),
        )


def build_soft_preference_registrations(
    preferences: tuple[SoftAggregatePreference, ...],
) -> tuple[ConstraintRegistration, ...]:
    """Bind configured SOFT aggregate preferences to canonical final validation."""

    if not isinstance(preferences, tuple):
        raise SoftPenaltyRuleError(
            "soft preferences must be an immutable tuple"
        )
    if any(
        not isinstance(preference, SoftAggregatePreference)
        for preference in preferences
    ):
        raise SoftPenaltyRuleError(
            "soft preferences must contain only SoftAggregatePreference values"
        )

    metric_ids = tuple(preference.metric_id for preference in preferences)
    if len(metric_ids) != len(set(metric_ids)):
        raise SoftPenaltyRuleError(
            "soft preferences must not contain duplicate metric IDs"
        )

    return tuple(
        ConstraintRegistration(
            stage=FINAL_VALIDATION_STAGE,
            constraint=SoftAggregatePreferenceConstraint(preference),
        )
        for preference in sorted(
            preferences,
            key=lambda item: item.metric_id.value,
        )
    )


def _failure_message(
    *,
    metric_id: RawMetricId,
    value: float,
    minimum: float | None,
    maximum: float | None,
) -> str | None:
    if minimum is not None and value < minimum:
        return (
            f"{metric_id.value}={value:.12g} is below preferred "
            f"minimum {minimum:.12g}"
        )
    if maximum is not None and value > maximum:
        return (
            f"{metric_id.value}={value:.12g} exceeds preferred "
            f"maximum {maximum:.12g}"
        )
    return None


def _require_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SoftPenaltyRuleError(f"{field_name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise SoftPenaltyRuleError(f"{field_name} must be a finite number")
    return numeric
