import math
import re
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.domain.benchmarking import (
    CANONICAL_METRIC_REGISTRY,
    MetricDirection,
    MetricValueKind,
    RawMetricId,
)


class MetricNormalizationError(ValueError):
    """Raised when a normalization policy or input violates the S11 contract."""


class NormalizationClampPolicy(StrEnum):
    """How scalar values outside the configured normalization range are handled."""

    CLAMP = "CLAMP"
    REJECT = "REJECT"


class NormalizationMissingPolicy(StrEnum):
    """How a missing scalar raw value is represented after normalization."""

    REJECT = "REJECT"
    PROPAGATE = "PROPAGATE"
    AS_WORST = "AS_WORST"


@dataclass(frozen=True, slots=True)
class MetricNormalizationPolicy:
    """Versioned normalization semantics for one canonical scalar raw metric."""

    metric_id: RawMetricId
    direction: MetricDirection
    lower_bound: float
    upper_bound: float
    clamp_policy: NormalizationClampPolicy
    missing_policy: NormalizationMissingPolicy
    version: str
    target_lower_bound: float | None = None
    target_upper_bound: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise MetricNormalizationError("metric_id must be a RawMetricId value")
        if not isinstance(self.direction, MetricDirection):
            raise MetricNormalizationError("direction must be a MetricDirection value")
        if not isinstance(self.clamp_policy, NormalizationClampPolicy):
            raise MetricNormalizationError(
                "clamp_policy must be a NormalizationClampPolicy value"
            )
        if not isinstance(self.missing_policy, NormalizationMissingPolicy):
            raise MetricNormalizationError(
                "missing_policy must be a NormalizationMissingPolicy value"
            )
        if (
            not isinstance(self.version, str)
            or _NORMALIZATION_VERSION_RE.fullmatch(self.version) is None
        ):
            raise MetricNormalizationError(
                "normalization version must be a stable non-empty identifier"
            )

        definition = CANONICAL_METRIC_REGISTRY.get(self.metric_id)
        if definition.value_kind is not MetricValueKind.SCALAR:
            raise MetricNormalizationError(
                "normalization policies are only valid for scalar raw metrics"
            )
        if definition.direction is MetricDirection.DESCRIPTIVE:
            raise MetricNormalizationError(
                "descriptive raw metrics do not have scalar normalization policies"
            )
        if self.direction is not definition.direction:
            raise MetricNormalizationError(
                "normalization direction must match canonical metric metadata"
            )

        lower = _require_finite_number("lower_bound", self.lower_bound)
        upper = _require_finite_number("upper_bound", self.upper_bound)
        if lower >= upper:
            raise MetricNormalizationError("lower_bound must be less than upper_bound")

        target_lower = self.target_lower_bound
        target_upper = self.target_upper_bound
        if self.direction is MetricDirection.TARGET:
            if target_lower is None or target_upper is None:
                raise MetricNormalizationError(
                    "TARGET normalization requires target_lower_bound and target_upper_bound"
                )
            target_lower_value = _require_finite_number(
                "target_lower_bound", target_lower
            )
            target_upper_value = _require_finite_number(
                "target_upper_bound", target_upper
            )
            if not lower <= target_lower_value <= target_upper_value <= upper:
                raise MetricNormalizationError(
                    "target bounds must satisfy lower_bound <= target_lower_bound <= "
                    "target_upper_bound <= upper_bound"
                )
        elif target_lower is not None or target_upper is not None:
            raise MetricNormalizationError(
                "target bounds are only valid for TARGET normalization"
            )


@dataclass(frozen=True, slots=True)
class NormalizedMetricValue:
    metric_id: RawMetricId
    raw_value: float | None
    normalized_value: float | None
    policy_version: str
    was_clamped: bool
    was_missing: bool

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise MetricNormalizationError("metric_id must be a RawMetricId value")
        if self.raw_value is not None:
            _require_finite_number("raw_value", self.raw_value)
        if self.normalized_value is not None:
            normalized = _require_finite_number(
                "normalized_value", self.normalized_value
            )
            if not 0.0 <= normalized <= 1.0:
                raise MetricNormalizationError(
                    "normalized_value must be within the inclusive [0, 1] range"
                )
        if (
            not isinstance(self.policy_version, str)
            or _NORMALIZATION_VERSION_RE.fullmatch(self.policy_version) is None
        ):
            raise MetricNormalizationError(
                "policy_version must be a stable non-empty identifier"
            )
        if not isinstance(self.was_clamped, bool):
            raise MetricNormalizationError("was_clamped must be boolean")
        if not isinstance(self.was_missing, bool):
            raise MetricNormalizationError("was_missing must be boolean")
        if self.was_missing != (self.raw_value is None):
            raise MetricNormalizationError(
                "was_missing must match whether raw_value is missing"
            )
        if self.was_clamped and self.was_missing:
            raise MetricNormalizationError("a missing value cannot also be clamped")


@dataclass(frozen=True, slots=True)
class MetricNormalizationProfile:
    """Deterministic versioned collection of per-metric normalization policies."""

    profile_id: str
    version: str
    policies: tuple[MetricNormalizationPolicy, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise MetricNormalizationError("profile_id must be non-empty")
        if (
            not isinstance(self.version, str)
            or _NORMALIZATION_VERSION_RE.fullmatch(self.version) is None
        ):
            raise MetricNormalizationError(
                "profile version must be a stable non-empty identifier"
            )
        if not isinstance(self.policies, tuple) or not self.policies:
            raise MetricNormalizationError(
                "normalization profile policies must be a non-empty tuple"
            )
        if any(
            not isinstance(policy, MetricNormalizationPolicy)
            for policy in self.policies
        ):
            raise MetricNormalizationError(
                "normalization profile must contain only MetricNormalizationPolicy values"
            )
        metric_ids = tuple(policy.metric_id for policy in self.policies)
        if len(metric_ids) != len(set(metric_ids)):
            raise MetricNormalizationError(
                "normalization profile must not contain duplicate metric IDs"
            )

    @property
    def metric_ids(self) -> tuple[RawMetricId, ...]:
        return tuple(policy.metric_id for policy in self.policies)

    def get(self, metric_id: RawMetricId) -> MetricNormalizationPolicy:
        if not isinstance(metric_id, RawMetricId):
            raise MetricNormalizationError(
                "normalization profile lookup requires a RawMetricId value"
            )
        for policy in self.policies:
            if policy.metric_id is metric_id:
                return policy
        raise MetricNormalizationError(
            f"normalization policy is not registered: {metric_id.value}"
        )

    def normalize(
        self,
        metric_id: RawMetricId,
        raw_value: float | int | None,
    ) -> NormalizedMetricValue:
        return normalize_metric(raw_value, policy=self.get(metric_id))


def normalize_metric(
    raw_value: float | int | None,
    *,
    policy: MetricNormalizationPolicy,
) -> NormalizedMetricValue:
    """Normalize one canonical scalar raw value to the inclusive [0, 1] range."""

    if not isinstance(policy, MetricNormalizationPolicy):
        raise MetricNormalizationError(
            "policy must be a MetricNormalizationPolicy value"
        )

    if raw_value is None:
        if policy.missing_policy is NormalizationMissingPolicy.REJECT:
            raise MetricNormalizationError(
                f"missing raw value is not allowed for {policy.metric_id.value}"
            )
        normalized = (
            0.0
            if policy.missing_policy is NormalizationMissingPolicy.AS_WORST
            else None
        )
        return NormalizedMetricValue(
            metric_id=policy.metric_id,
            raw_value=None,
            normalized_value=normalized,
            policy_version=policy.version,
            was_clamped=False,
            was_missing=True,
        )

    value = _require_finite_number("raw_value", raw_value)
    lower = float(policy.lower_bound)
    upper = float(policy.upper_bound)
    was_clamped = value < lower or value > upper
    if was_clamped:
        if policy.clamp_policy is NormalizationClampPolicy.REJECT:
            raise MetricNormalizationError(
                f"raw value for {policy.metric_id.value} is outside the configured "
                "normalization range"
            )
        effective_value = min(max(value, lower), upper)
    else:
        effective_value = value

    normalized = _normalize_present_value(effective_value, policy=policy)
    return NormalizedMetricValue(
        metric_id=policy.metric_id,
        raw_value=value,
        normalized_value=normalized,
        policy_version=policy.version,
        was_clamped=was_clamped,
        was_missing=False,
    )


def _normalize_present_value(
    value: float,
    *,
    policy: MetricNormalizationPolicy,
) -> float:
    lower = float(policy.lower_bound)
    upper = float(policy.upper_bound)

    if policy.direction is MetricDirection.HIGHER_IS_BETTER:
        return (value - lower) / (upper - lower)
    if policy.direction is MetricDirection.LOWER_IS_BETTER:
        return (upper - value) / (upper - lower)
    if policy.direction is MetricDirection.TARGET:
        target_lower = policy.target_lower_bound
        target_upper = policy.target_upper_bound
        assert target_lower is not None
        assert target_upper is not None
        target_lower_value = float(target_lower)
        target_upper_value = float(target_upper)
        if target_lower_value <= value <= target_upper_value:
            return 1.0
        if value < target_lower_value:
            return (value - lower) / (target_lower_value - lower)
        return (upper - value) / (upper - target_upper_value)

    raise MetricNormalizationError(
        f"unsupported normalization direction: {policy.direction.value}"
    )


def _require_finite_number(field_name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricNormalizationError(f"{field_name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise MetricNormalizationError(f"{field_name} must be finite")
    return numeric


_NORMALIZATION_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
