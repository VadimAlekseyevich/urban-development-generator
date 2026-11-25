import math
import re
from dataclasses import dataclass

from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.score import (
    CompositeScoreConfig,
    CompositeScoreMetricWeight,
)


class ScoreSensitivityError(ValueError):
    """Raised when score snapshots or weight perturbations are incompatible."""


@dataclass(frozen=True, slots=True)
class ScoreSensitivityMetricSnapshot:
    """Persisted scalar state sufficient for weight-only sensitivity."""

    metric_id: RawMetricId
    raw_value: float | None
    normalized_value: float | None
    normalization_policy_version: str
    was_clamped: bool
    was_missing: bool

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise ScoreSensitivityError("metric_id must be a RawMetricId value")
        if self.raw_value is not None:
            _require_finite_number("raw_value", self.raw_value)
        if self.normalized_value is not None:
            normalized = _require_finite_number(
                "normalized_value", self.normalized_value
            )
            if not 0.0 <= normalized <= 1.0:
                raise ScoreSensitivityError(
                    "normalized_value must be within the inclusive [0, 1] range"
                )
        if (
            not isinstance(self.normalization_policy_version, str)
            or _VERSION_RE.fullmatch(self.normalization_policy_version) is None
        ):
            raise ScoreSensitivityError(
                "normalization_policy_version must be a stable non-empty identifier"
            )
        if not isinstance(self.was_clamped, bool):
            raise ScoreSensitivityError("was_clamped must be boolean")
        if not isinstance(self.was_missing, bool):
            raise ScoreSensitivityError("was_missing must be boolean")
        if self.was_missing != (self.raw_value is None):
            raise ScoreSensitivityError(
                "was_missing must match whether raw_value is missing"
            )
        if self.was_clamped and self.was_missing:
            raise ScoreSensitivityError("a missing value cannot also be clamped")
        if self.normalized_value is None and not self.was_missing:
            raise ScoreSensitivityError(
                "present raw values must have a persisted normalized value"
            )


@dataclass(frozen=True, slots=True)
class ScoreSensitivityRunSnapshot:
    """One run's persisted evaluation snapshot with no GIS/domain objects."""

    run_ref: str
    normalization_profile_id: str
    normalization_profile_version: str
    metrics: tuple[ScoreSensitivityMetricSnapshot, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run_ref, str) or not self.run_ref.strip():
            raise ScoreSensitivityError("run_ref must be non-empty")
        if (
            not isinstance(self.normalization_profile_id, str)
            or not self.normalization_profile_id.strip()
        ):
            raise ScoreSensitivityError("normalization_profile_id must be non-empty")
        if (
            not isinstance(self.normalization_profile_version, str)
            or _VERSION_RE.fullmatch(self.normalization_profile_version) is None
        ):
            raise ScoreSensitivityError(
                "normalization_profile_version must be a stable non-empty identifier"
            )
        if not isinstance(self.metrics, tuple) or not self.metrics:
            raise ScoreSensitivityError("metrics must be a non-empty tuple")
        if any(
            not isinstance(item, ScoreSensitivityMetricSnapshot)
            for item in self.metrics
        ):
            raise ScoreSensitivityError(
                "metrics must contain only ScoreSensitivityMetricSnapshot values"
            )
        metric_ids = tuple(item.metric_id for item in self.metrics)
        if len(metric_ids) != len(set(metric_ids)):
            raise ScoreSensitivityError(
                "run sensitivity snapshot must not contain duplicate metric IDs"
            )


@dataclass(frozen=True, slots=True)
class ScoreSensitivityWeightFactor:
    metric_id: RawMetricId
    factor: float

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise ScoreSensitivityError("metric_id must be a RawMetricId value")
        factor = _require_finite_number("factor", self.factor)
        if factor < 0.0:
            raise ScoreSensitivityError("weight factor must be non-negative")


@dataclass(frozen=True, slots=True)
class ScoreWeightPerturbation:
    perturbation_id: str
    version: str
    factors: tuple[ScoreSensitivityWeightFactor, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.perturbation_id, str)
            or _VERSION_RE.fullmatch(self.perturbation_id) is None
        ):
            raise ScoreSensitivityError(
                "perturbation_id must be a stable non-empty identifier"
            )
        if (
            not isinstance(self.version, str)
            or _VERSION_RE.fullmatch(self.version) is None
        ):
            raise ScoreSensitivityError(
                "perturbation version must be a stable non-empty identifier"
            )
        if not isinstance(self.factors, tuple) or not self.factors:
            raise ScoreSensitivityError(
                "perturbation factors must be a non-empty tuple"
            )
        if any(
            not isinstance(item, ScoreSensitivityWeightFactor)
            for item in self.factors
        ):
            raise ScoreSensitivityError(
                "perturbation factors must contain only "
                "ScoreSensitivityWeightFactor values"
            )
        metric_ids = tuple(item.metric_id for item in self.factors)
        if len(metric_ids) != len(set(metric_ids)):
            raise ScoreSensitivityError(
                "perturbation factors must not contain duplicate metric IDs"
            )


@dataclass(frozen=True, slots=True)
class ScoreSensitivityRankedRun:
    run_ref: str
    score: float
    rank: int
    baseline_score: float
    baseline_rank: int
    score_delta: float
    rank_delta: int


@dataclass(frozen=True, slots=True)
class ScoreSensitivityScenarioResult:
    perturbation_id: str
    perturbation_version: str
    weights: tuple[CompositeScoreMetricWeight, ...]
    ranking: tuple[ScoreSensitivityRankedRun, ...]


@dataclass(frozen=True, slots=True)
class ScoreSensitivityResult:
    normalization_profile_id: str
    normalization_profile_version: str
    baseline_config_id: str
    baseline_config_version: str
    baseline_ranking: tuple[ScoreSensitivityRankedRun, ...]
    scenarios: tuple[ScoreSensitivityScenarioResult, ...]


def analyze_score_sensitivity(
    runs: tuple[ScoreSensitivityRunSnapshot, ...],
    *,
    baseline_config: CompositeScoreConfig,
    perturbations: tuple[ScoreWeightPerturbation, ...],
) -> ScoreSensitivityResult:
    """Recalculate rankings from persisted normalized score inputs only."""

    if not isinstance(runs, tuple) or not runs:
        raise ScoreSensitivityError("runs must be a non-empty tuple")
    if any(not isinstance(item, ScoreSensitivityRunSnapshot) for item in runs):
        raise ScoreSensitivityError(
            "runs must contain only ScoreSensitivityRunSnapshot values"
        )
    if not isinstance(baseline_config, CompositeScoreConfig):
        raise ScoreSensitivityError(
            "baseline_config must be a CompositeScoreConfig value"
        )
    if not isinstance(perturbations, tuple) or not perturbations:
        raise ScoreSensitivityError("perturbations must be a non-empty tuple")
    if any(
        not isinstance(item, ScoreWeightPerturbation) for item in perturbations
    ):
        raise ScoreSensitivityError(
            "perturbations must contain only ScoreWeightPerturbation values"
        )

    run_refs = tuple(item.run_ref for item in runs)
    if len(run_refs) != len(set(run_refs)):
        raise ScoreSensitivityError(
            "runs must not contain duplicate run_ref values"
        )
    perturbation_ids = tuple(item.perturbation_id for item in perturbations)
    if len(perturbation_ids) != len(set(perturbation_ids)):
        raise ScoreSensitivityError(
            "perturbations must not contain duplicate perturbation IDs"
        )

    profile_id = runs[0].normalization_profile_id
    profile_version = runs[0].normalization_profile_version
    metric_ids = tuple(
        sorted(
            (item.metric_id for item in runs[0].metrics),
            key=lambda metric_id: metric_id.value,
        )
    )
    expected_ids = set(metric_ids)
    if set(baseline_config.metric_ids) != expected_ids:
        raise ScoreSensitivityError(
            "baseline score weights must match persisted metric IDs exactly"
        )

    policy_versions = {
        item.metric_id: item.normalization_policy_version
        for item in runs[0].metrics
    }
    snapshots: dict[
        str,
        dict[RawMetricId, ScoreSensitivityMetricSnapshot],
    ] = {}
    for run in runs:
        if (
            run.normalization_profile_id != profile_id
            or run.normalization_profile_version != profile_version
        ):
            raise ScoreSensitivityError(
                "all runs must use the same normalization profile identity/version"
            )
        by_id = {item.metric_id: item for item in run.metrics}
        if set(by_id) != expected_ids:
            raise ScoreSensitivityError(
                "all runs must contain the same persisted metric IDs"
            )
        for metric_id in metric_ids:
            if (
                by_id[metric_id].normalization_policy_version
                != policy_versions[metric_id]
            ):
                raise ScoreSensitivityError(
                    "all runs must use the same normalization policy version "
                    f"for {metric_id.value}"
                )
        snapshots[run.run_ref] = by_id

    baseline_scores = {
        run.run_ref: _score_snapshot(
            snapshots[run.run_ref],
            metric_ids=metric_ids,
            config=baseline_config,
        )
        for run in runs
    }
    baseline_positions = _rank_scores(baseline_scores)
    baseline_ranking = tuple(
        ScoreSensitivityRankedRun(
            run_ref=run_ref,
            score=score,
            rank=rank,
            baseline_score=score,
            baseline_rank=rank,
            score_delta=0.0,
            rank_delta=0,
        )
        for rank, (run_ref, score) in enumerate(
            baseline_positions,
            start=1,
        )
    )
    baseline_by_ref = {
        item.run_ref: item for item in baseline_ranking
    }

    scenarios: list[ScoreSensitivityScenarioResult] = []
    for perturbation in perturbations:
        config = build_perturbed_score_config(
            baseline_config,
            perturbation=perturbation,
        )
        scores = {
            run.run_ref: _score_snapshot(
                snapshots[run.run_ref],
                metric_ids=metric_ids,
                config=config,
            )
            for run in runs
        }
        positions = _rank_scores(scores)
        ranking = tuple(
            ScoreSensitivityRankedRun(
                run_ref=run_ref,
                score=score,
                rank=rank,
                baseline_score=baseline_by_ref[run_ref].score,
                baseline_rank=baseline_by_ref[run_ref].rank,
                score_delta=score - baseline_by_ref[run_ref].score,
                rank_delta=baseline_by_ref[run_ref].rank - rank,
            )
            for rank, (run_ref, score) in enumerate(positions, start=1)
        )
        scenarios.append(
            ScoreSensitivityScenarioResult(
                perturbation_id=perturbation.perturbation_id,
                perturbation_version=perturbation.version,
                weights=config.weights,
                ranking=ranking,
            )
        )

    return ScoreSensitivityResult(
        normalization_profile_id=profile_id,
        normalization_profile_version=profile_version,
        baseline_config_id=baseline_config.config_id,
        baseline_config_version=baseline_config.version,
        baseline_ranking=baseline_ranking,
        scenarios=tuple(scenarios),
    )


def build_perturbed_score_config(
    baseline_config: CompositeScoreConfig,
    *,
    perturbation: ScoreWeightPerturbation,
) -> CompositeScoreConfig:
    """Apply complete non-negative multipliers to one baseline score config."""

    if not isinstance(baseline_config, CompositeScoreConfig):
        raise ScoreSensitivityError(
            "baseline_config must be a CompositeScoreConfig value"
        )
    if not isinstance(perturbation, ScoreWeightPerturbation):
        raise ScoreSensitivityError(
            "perturbation must be a ScoreWeightPerturbation value"
        )

    factor_by_id = {
        item.metric_id: item.factor for item in perturbation.factors
    }
    if set(factor_by_id) != set(baseline_config.metric_ids):
        raise ScoreSensitivityError(
            "perturbation factor IDs must match baseline score metric IDs exactly"
        )

    weights = tuple(
        CompositeScoreMetricWeight(
            metric_id=item.metric_id,
            weight=item.weight * factor_by_id[item.metric_id],
        )
        for item in baseline_config.weights
    )
    try:
        return CompositeScoreConfig(
            config_id=(
                f"{baseline_config.config_id}@{perturbation.perturbation_id}"
            ),
            version=f"{baseline_config.version}-{perturbation.version}",
            weights=weights,
        )
    except ValueError as exc:
        raise ScoreSensitivityError(
            "perturbation must leave at least one positive score weight"
        ) from exc


def _score_snapshot(
    metrics: dict[RawMetricId, ScoreSensitivityMetricSnapshot],
    *,
    metric_ids: tuple[RawMetricId, ...],
    config: CompositeScoreConfig,
) -> float:
    weight_by_id = {
        item.metric_id: item.weight for item in config.weights
    }
    total_weight = math.fsum(weight_by_id.values())
    contributions: list[float] = []
    for metric_id in metric_ids:
        normalized = metrics[metric_id].normalized_value
        normalized_weight = weight_by_id[metric_id] / total_weight
        if normalized is None:
            if normalized_weight > 0.0:
                raise ScoreSensitivityError(
                    "missing persisted normalized value has positive score weight: "
                    f"{metric_id.value}"
                )
            contributions.append(0.0)
            continue
        contributions.append(normalized * normalized_weight)
    return math.fsum(contributions)


def _rank_scores(
    scores: dict[str, float],
) -> tuple[tuple[str, float], ...]:
    return tuple(
        sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )


def _require_finite_number(
    field_name: str,
    value: float | int,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoreSensitivityError(
            f"{field_name} must be a finite number"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ScoreSensitivityError(f"{field_name} must be finite")
    return numeric


_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
