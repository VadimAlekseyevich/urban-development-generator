import math
import re
from dataclasses import dataclass

from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.normalization import (
    MetricNormalizationProfile,
    NormalizedMetricValue,
)


class CompositeScoreError(ValueError):
    """Raised when composite-score configuration or inputs violate the S11 contract."""


@dataclass(frozen=True, slots=True)
class CompositeScoreRawMetric:
    """One canonical scalar raw metric consumed by composite scoring."""

    metric_id: RawMetricId
    raw_value: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise CompositeScoreError("metric_id must be a RawMetricId value")
        if self.raw_value is not None:
            _require_finite_number("raw_value", self.raw_value)


@dataclass(frozen=True, slots=True)
class CompositeScoreMetricWeight:
    """Configured non-negative weight for one normalized metric."""

    metric_id: RawMetricId
    weight: float

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise CompositeScoreError("metric_id must be a RawMetricId value")
        weight = _require_finite_number("weight", self.weight)
        if weight < 0.0:
            raise CompositeScoreError("weight must be non-negative")


@dataclass(frozen=True, slots=True)
class CompositeScoreConfig:
    """Versioned score configuration over one normalization profile."""

    config_id: str
    version: str
    weights: tuple[CompositeScoreMetricWeight, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.config_id, str) or not self.config_id.strip():
            raise CompositeScoreError("config_id must be non-empty")
        if (
            not isinstance(self.version, str)
            or _SCORE_VERSION_RE.fullmatch(self.version) is None
        ):
            raise CompositeScoreError(
                "score config version must be a stable non-empty identifier"
            )
        if not isinstance(self.weights, tuple) or not self.weights:
            raise CompositeScoreError("score weights must be a non-empty tuple")
        if any(
            not isinstance(item, CompositeScoreMetricWeight) for item in self.weights
        ):
            raise CompositeScoreError(
                "score weights must contain only CompositeScoreMetricWeight values"
            )

        metric_ids = tuple(item.metric_id for item in self.weights)
        if len(metric_ids) != len(set(metric_ids)):
            raise CompositeScoreError("score weights must not contain duplicate metric IDs")
        if math.fsum(item.weight for item in self.weights) <= 0.0:
            raise CompositeScoreError("score weights must have positive total weight")

    @property
    def metric_ids(self) -> tuple[RawMetricId, ...]:
        return tuple(item.metric_id for item in self.weights)


@dataclass(frozen=True, slots=True)
class CompositeScoreMetricResult:
    metric_id: RawMetricId
    raw_value: float | None
    normalized_value: float | None
    normalization_policy_version: str
    configured_weight: float
    normalized_weight: float
    contribution: float
    was_clamped: bool
    was_missing: bool

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise CompositeScoreError("metric_id must be a RawMetricId value")
        if self.raw_value is not None:
            _require_finite_number("raw_value", self.raw_value)
        if self.normalized_value is not None:
            normalized = _require_finite_number(
                "normalized_value", self.normalized_value
            )
            if not 0.0 <= normalized <= 1.0:
                raise CompositeScoreError(
                    "normalized_value must be within the inclusive [0, 1] range"
                )
        configured_weight = _require_finite_number(
            "configured_weight", self.configured_weight
        )
        normalized_weight = _require_finite_number(
            "normalized_weight", self.normalized_weight
        )
        contribution = _require_finite_number("contribution", self.contribution)
        if configured_weight < 0.0:
            raise CompositeScoreError("configured_weight must be non-negative")
        if not 0.0 <= normalized_weight <= 1.0:
            raise CompositeScoreError(
                "normalized_weight must be within the inclusive [0, 1] range"
            )
        if not 0.0 <= contribution <= 1.0:
            raise CompositeScoreError(
                "contribution must be within the inclusive [0, 1] range"
            )
        if (
            not isinstance(self.normalization_policy_version, str)
            or _SCORE_VERSION_RE.fullmatch(self.normalization_policy_version) is None
        ):
            raise CompositeScoreError(
                "normalization_policy_version must be a stable non-empty identifier"
            )
        if not isinstance(self.was_clamped, bool):
            raise CompositeScoreError("was_clamped must be boolean")
        if not isinstance(self.was_missing, bool):
            raise CompositeScoreError("was_missing must be boolean")


@dataclass(frozen=True, slots=True)
class CompositeScoreResult:
    score: float
    score_config_id: str
    score_config_version: str
    normalization_profile_id: str
    normalization_profile_version: str
    metrics: tuple[CompositeScoreMetricResult, ...]

    def __post_init__(self) -> None:
        score = _require_finite_number("score", self.score)
        if not 0.0 <= score <= 1.0:
            raise CompositeScoreError("score must be within the inclusive [0, 1] range")
        if not isinstance(self.score_config_id, str) or not self.score_config_id.strip():
            raise CompositeScoreError("score_config_id must be non-empty")
        for field_name, value in (
            ("score_config_version", self.score_config_version),
            ("normalization_profile_version", self.normalization_profile_version),
        ):
            if not isinstance(value, str) or _SCORE_VERSION_RE.fullmatch(value) is None:
                raise CompositeScoreError(
                    f"{field_name} must be a stable non-empty identifier"
                )
        if (
            not isinstance(self.normalization_profile_id, str)
            or not self.normalization_profile_id.strip()
        ):
            raise CompositeScoreError("normalization_profile_id must be non-empty")
        if not isinstance(self.metrics, tuple) or not self.metrics:
            raise CompositeScoreError("metrics must be a non-empty tuple")
        if any(
            not isinstance(item, CompositeScoreMetricResult) for item in self.metrics
        ):
            raise CompositeScoreError(
                "metrics must contain only CompositeScoreMetricResult values"
            )

        metric_ids = tuple(item.metric_id for item in self.metrics)
        if len(metric_ids) != len(set(metric_ids)):
            raise CompositeScoreError("metrics must not contain duplicate metric IDs")
        if not math.isclose(
            math.fsum(item.normalized_weight for item in self.metrics),
            1.0,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise CompositeScoreError("normalized metric weights must sum to 1")
        if not math.isclose(
            math.fsum(item.contribution for item in self.metrics),
            score,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise CompositeScoreError("score must equal the sum of metric contributions")


def build_composite_score(
    raw_metrics: tuple[CompositeScoreRawMetric, ...],
    *,
    normalization_profile: MetricNormalizationProfile,
    config: CompositeScoreConfig,
) -> CompositeScoreResult:
    """Build one deterministic weighted score while preserving every raw score input."""

    if not isinstance(raw_metrics, tuple) or not raw_metrics:
        raise CompositeScoreError("raw_metrics must be a non-empty tuple")
    if any(not isinstance(item, CompositeScoreRawMetric) for item in raw_metrics):
        raise CompositeScoreError(
            "raw_metrics must contain only CompositeScoreRawMetric values"
        )
    if not isinstance(normalization_profile, MetricNormalizationProfile):
        raise CompositeScoreError(
            "normalization_profile must be a MetricNormalizationProfile value"
        )
    if not isinstance(config, CompositeScoreConfig):
        raise CompositeScoreError("config must be a CompositeScoreConfig value")

    raw_by_id = {item.metric_id: item for item in raw_metrics}
    if len(raw_by_id) != len(raw_metrics):
        raise CompositeScoreError(
            "raw_metrics must not contain duplicate metric IDs"
        )
    weight_by_id = {item.metric_id: item for item in config.weights}
    profile_ids = normalization_profile.metric_ids
    expected = set(profile_ids)
    if set(raw_by_id) != expected:
        raise CompositeScoreError(
            "raw metric IDs must match the normalization profile exactly"
        )
    if set(weight_by_id) != expected:
        raise CompositeScoreError(
            "score weight IDs must match the normalization profile exactly"
        )

    total_weight = math.fsum(item.weight for item in config.weights)
    metric_results: list[CompositeScoreMetricResult] = []
    for metric_id in profile_ids:
        raw = raw_by_id[metric_id]
        weight = weight_by_id[metric_id]
        normalized = normalization_profile.normalize(metric_id, raw.raw_value)
        normalized_weight = weight.weight / total_weight
        contribution = _contribution(
            metric_id=metric_id,
            normalized=normalized,
            normalized_weight=normalized_weight,
        )
        metric_results.append(
            CompositeScoreMetricResult(
                metric_id=metric_id,
                raw_value=normalized.raw_value,
                normalized_value=normalized.normalized_value,
                normalization_policy_version=normalized.policy_version,
                configured_weight=weight.weight,
                normalized_weight=normalized_weight,
                contribution=contribution,
                was_clamped=normalized.was_clamped,
                was_missing=normalized.was_missing,
            )
        )

    metrics = tuple(metric_results)
    return CompositeScoreResult(
        score=math.fsum(item.contribution for item in metrics),
        score_config_id=config.config_id,
        score_config_version=config.version,
        normalization_profile_id=normalization_profile.profile_id,
        normalization_profile_version=normalization_profile.version,
        metrics=metrics,
    )


def _contribution(
    *,
    metric_id: RawMetricId,
    normalized: NormalizedMetricValue,
    normalized_weight: float,
) -> float:
    if normalized.normalized_value is None:
        if normalized_weight > 0.0:
            raise CompositeScoreError(
                "missing normalized value with positive score weight is not allowed: "
                f"{metric_id.value}"
            )
        return 0.0
    return normalized.normalized_value * normalized_weight


def _require_finite_number(field_name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompositeScoreError(f"{field_name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise CompositeScoreError(f"{field_name} must be finite")
    return numeric


_SCORE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
