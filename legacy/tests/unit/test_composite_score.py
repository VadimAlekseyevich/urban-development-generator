import pytest

from core.urban_generator.domain.benchmarking import MetricDirection, RawMetricId
from core.urban_generator.metrics.normalization import (
    MetricNormalizationPolicy,
    MetricNormalizationProfile,
    NormalizationClampPolicy,
    NormalizationMissingPolicy,
)
from core.urban_generator.metrics.score import (
    CompositeScoreConfig,
    CompositeScoreError,
    CompositeScoreMetricWeight,
    CompositeScoreRawMetric,
    build_composite_score,
)


def _policy(
    metric_id: RawMetricId,
    direction: MetricDirection,
    *,
    lower: float,
    upper: float,
    missing: NormalizationMissingPolicy = NormalizationMissingPolicy.REJECT,
    target_lower: float | None = None,
    target_upper: float | None = None,
) -> MetricNormalizationPolicy:
    return MetricNormalizationPolicy(
        metric_id=metric_id,
        direction=direction,
        lower_bound=lower,
        upper_bound=upper,
        clamp_policy=NormalizationClampPolicy.CLAMP,
        missing_policy=missing,
        version=f"{metric_id.value}-v1",
        target_lower_bound=target_lower,
        target_upper_bound=target_upper,
    )


def _profile() -> MetricNormalizationProfile:
    return MetricNormalizationProfile(
        profile_id="evaluation-default",
        version="1",
        policies=(
            _policy(
                RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
                MetricDirection.HIGHER_IS_BETTER,
                lower=0.0,
                upper=1.0,
            ),
            _policy(
                RawMetricId.ROADS_CIRCUITY,
                MetricDirection.LOWER_IS_BETTER,
                lower=1.0,
                upper=2.0,
                missing=NormalizationMissingPolicy.PROPAGATE,
            ),
            _policy(
                RawMetricId.BUILDINGS_FAR,
                MetricDirection.TARGET,
                lower=0.0,
                upper=4.0,
                target_lower=1.5,
                target_upper=2.5,
            ),
        ),
    )


def _config(
    *,
    circuity_weight: float = 1.0,
) -> CompositeScoreConfig:
    return CompositeScoreConfig(
        config_id="score-default",
        version="1",
        weights=(
            CompositeScoreMetricWeight(
                RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
                2.0,
            ),
            CompositeScoreMetricWeight(
                RawMetricId.ROADS_CIRCUITY,
                circuity_weight,
            ),
            CompositeScoreMetricWeight(
                RawMetricId.BUILDINGS_FAR,
                1.0,
            ),
        ),
    )


def _raw_metrics(
    *,
    circuity: float | None = 1.2,
) -> tuple[CompositeScoreRawMetric, ...]:
    return (
        CompositeScoreRawMetric(
            RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
            0.8,
        ),
        CompositeScoreRawMetric(RawMetricId.ROADS_CIRCUITY, circuity),
        CompositeScoreRawMetric(RawMetricId.BUILDINGS_FAR, 2.0),
    )


def test_composite_score_preserves_raw_normalized_weight_and_contribution() -> None:
    result = build_composite_score(
        _raw_metrics(),
        normalization_profile=_profile(),
        config=_config(),
    )

    assert result.score == pytest.approx(0.85)
    assert result.score_config_id == "score-default"
    assert result.normalization_profile_id == "evaluation-default"
    assert tuple(item.metric_id for item in result.metrics) == _profile().metric_ids

    coverage, circuity, far = result.metrics
    assert coverage.raw_value == pytest.approx(0.8)
    assert coverage.normalized_value == pytest.approx(0.8)
    assert coverage.configured_weight == pytest.approx(2.0)
    assert coverage.normalized_weight == pytest.approx(0.5)
    assert coverage.contribution == pytest.approx(0.4)

    assert circuity.normalized_value == pytest.approx(0.8)
    assert circuity.normalized_weight == pytest.approx(0.25)
    assert circuity.contribution == pytest.approx(0.2)

    assert far.normalized_value == pytest.approx(1.0)
    assert far.normalized_weight == pytest.approx(0.25)
    assert far.contribution == pytest.approx(0.25)


def test_score_is_invariant_to_raw_and_weight_input_permutation() -> None:
    raw = _raw_metrics()
    config = _config()
    permuted_config = CompositeScoreConfig(
        config_id=config.config_id,
        version=config.version,
        weights=tuple(reversed(config.weights)),
    )

    expected = build_composite_score(
        raw,
        normalization_profile=_profile(),
        config=config,
    )
    actual = build_composite_score(
        tuple(reversed(raw)),
        normalization_profile=_profile(),
        config=permuted_config,
    )

    assert actual == expected


def test_propagated_missing_value_requires_zero_score_weight() -> None:
    with pytest.raises(
        CompositeScoreError,
        match="missing normalized value with positive score weight",
    ):
        build_composite_score(
            _raw_metrics(circuity=None),
            normalization_profile=_profile(),
            config=_config(),
        )

    result = build_composite_score(
        _raw_metrics(circuity=None),
        normalization_profile=_profile(),
        config=_config(circuity_weight=0.0),
    )
    circuity = result.metrics[1]
    assert circuity.raw_value is None
    assert circuity.normalized_value is None
    assert circuity.was_missing is True
    assert circuity.normalized_weight == 0.0
    assert circuity.contribution == 0.0
    assert result.score == pytest.approx((0.8 * 2.0 + 1.0) / 3.0)


def test_score_config_rejects_negative_duplicate_and_all_zero_weights() -> None:
    with pytest.raises(CompositeScoreError, match="non-negative"):
        CompositeScoreMetricWeight(RawMetricId.BUILDINGS_FAR, -1.0)

    weight = CompositeScoreMetricWeight(RawMetricId.BUILDINGS_FAR, 1.0)
    with pytest.raises(CompositeScoreError, match="duplicate"):
        CompositeScoreConfig(
            config_id="duplicate",
            version="1",
            weights=(weight, weight),
        )

    with pytest.raises(CompositeScoreError, match="positive total"):
        CompositeScoreConfig(
            config_id="zero",
            version="1",
            weights=(
                CompositeScoreMetricWeight(RawMetricId.BUILDINGS_FAR, 0.0),
            ),
        )


def test_score_requires_exact_metric_coverage_for_profile_and_weights() -> None:
    with pytest.raises(CompositeScoreError, match="raw metric IDs"):
        build_composite_score(
            _raw_metrics()[:-1],
            normalization_profile=_profile(),
            config=_config(),
        )

    config = CompositeScoreConfig(
        config_id="partial",
        version="1",
        weights=_config().weights[:-1],
    )
    with pytest.raises(CompositeScoreError, match="score weight IDs"):
        build_composite_score(
            _raw_metrics(),
            normalization_profile=_profile(),
            config=config,
        )


def test_score_rejects_non_finite_raw_values_and_malformed_versions() -> None:
    with pytest.raises(CompositeScoreError, match="raw_value"):
        CompositeScoreRawMetric(
            RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
            float("nan"),
        )

    with pytest.raises(CompositeScoreError, match="version"):
        CompositeScoreConfig(
            config_id="broken",
            version="bad version",
            weights=(
                CompositeScoreMetricWeight(RawMetricId.BUILDINGS_FAR, 1.0),
            ),
        )
