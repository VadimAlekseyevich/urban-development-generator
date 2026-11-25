import pytest

from core.urban_generator.domain.benchmarking import MetricDirection, RawMetricId
from core.urban_generator.metrics.normalization import (
    MetricNormalizationError,
    MetricNormalizationPolicy,
    MetricNormalizationProfile,
    NormalizationClampPolicy,
    NormalizationMissingPolicy,
    normalize_metric,
)


def _policy(
    metric_id: RawMetricId = RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER,
    **kwargs: object,
) -> MetricNormalizationPolicy:
    params: dict[str, object] = {
        "metric_id": metric_id,
        "direction": direction,
        "lower_bound": 0.0,
        "upper_bound": 1.0,
        "clamp_policy": NormalizationClampPolicy.CLAMP,
        "missing_policy": NormalizationMissingPolicy.REJECT,
        "version": "1",
    }
    params.update(kwargs)
    return MetricNormalizationPolicy(**params)  # type: ignore[arg-type]


def test_higher_and_lower_direction_are_linear_and_bounded() -> None:
    higher = _policy()
    assert normalize_metric(0.0, policy=higher).normalized_value == 0.0
    assert normalize_metric(0.25, policy=higher).normalized_value == 0.25
    assert normalize_metric(1.0, policy=higher).normalized_value == 1.0

    lower = _policy(
        metric_id=RawMetricId.ROADS_CIRCUITY,
        direction=MetricDirection.LOWER_IS_BETTER,
        lower_bound=1.0,
        upper_bound=2.0,
    )
    assert normalize_metric(1.0, policy=lower).normalized_value == 1.0
    assert normalize_metric(1.25, policy=lower).normalized_value == 0.75
    assert normalize_metric(2.0, policy=lower).normalized_value == 0.0


def test_target_direction_uses_plateau_and_linear_shoulders() -> None:
    policy = _policy(
        metric_id=RawMetricId.BUILDINGS_FAR,
        direction=MetricDirection.TARGET,
        lower_bound=0.0,
        upper_bound=4.0,
        target_lower_bound=1.5,
        target_upper_bound=2.5,
    )
    assert normalize_metric(0.0, policy=policy).normalized_value == 0.0
    assert normalize_metric(0.75, policy=policy).normalized_value == 0.5
    assert normalize_metric(1.5, policy=policy).normalized_value == 1.0
    assert normalize_metric(2.0, policy=policy).normalized_value == 1.0
    assert normalize_metric(2.5, policy=policy).normalized_value == 1.0
    assert normalize_metric(3.25, policy=policy).normalized_value == 0.5
    assert normalize_metric(4.0, policy=policy).normalized_value == 0.0


def test_out_of_range_value_is_clamped_or_rejected_explicitly() -> None:
    clamped = normalize_metric(1.5, policy=_policy())
    assert clamped.raw_value == 1.5
    assert clamped.normalized_value == 1.0
    assert clamped.was_clamped is True

    reject = _policy(clamp_policy=NormalizationClampPolicy.REJECT)
    with pytest.raises(MetricNormalizationError, match="outside"):
        normalize_metric(1.5, policy=reject)


def test_missing_policy_is_explicit_and_never_inferred_from_raw_zero() -> None:
    propagate = _policy(missing_policy=NormalizationMissingPolicy.PROPAGATE)
    value = normalize_metric(None, policy=propagate)
    assert value.normalized_value is None
    assert value.was_missing is True

    worst = _policy(missing_policy=NormalizationMissingPolicy.AS_WORST)
    value = normalize_metric(None, policy=worst)
    assert value.normalized_value == 0.0
    assert value.was_missing is True

    with pytest.raises(MetricNormalizationError, match="missing raw value"):
        normalize_metric(None, policy=_policy())


def test_policy_must_match_canonical_scalar_direction_and_valid_range() -> None:
    with pytest.raises(MetricNormalizationError, match="match canonical"):
        _policy(direction=MetricDirection.LOWER_IS_BETTER)

    with pytest.raises(MetricNormalizationError, match="scalar"):
        _policy(
            metric_id=RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION,
            direction=MetricDirection.DESCRIPTIVE,
        )

    with pytest.raises(MetricNormalizationError, match="less than"):
        _policy(lower_bound=1.0, upper_bound=1.0)

    with pytest.raises(MetricNormalizationError, match="requires target"):
        _policy(
            metric_id=RawMetricId.BUILDINGS_FAR,
            direction=MetricDirection.TARGET,
        )


def test_target_bounds_must_be_inside_normalization_range() -> None:
    with pytest.raises(MetricNormalizationError, match="target bounds"):
        _policy(
            metric_id=RawMetricId.BUILDINGS_FAR,
            direction=MetricDirection.TARGET,
            lower_bound=0.0,
            upper_bound=4.0,
            target_lower_bound=-1.0,
            target_upper_bound=2.0,
        )


def test_non_finite_or_boolean_input_is_rejected() -> None:
    policy = _policy()
    for value in (float("nan"), float("inf"), True):
        with pytest.raises(MetricNormalizationError):
            normalize_metric(value, policy=policy)


def test_profile_is_versioned_deterministic_and_rejects_duplicates() -> None:
    higher = _policy(version="coverage-v1")
    lower = _policy(
        metric_id=RawMetricId.ROADS_CIRCUITY,
        direction=MetricDirection.LOWER_IS_BETTER,
        lower_bound=1.0,
        upper_bound=2.0,
        missing_policy=NormalizationMissingPolicy.PROPAGATE,
        version="circuity-v1",
    )
    profile = MetricNormalizationProfile(
        profile_id="research-default",
        version="1",
        policies=(higher, lower),
    )

    assert profile.metric_ids == (
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
        RawMetricId.ROADS_CIRCUITY,
    )
    assert profile.get(RawMetricId.ROADS_CIRCUITY) is lower
    assert (
        profile.normalize(
            RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
            0.4,
        ).normalized_value
        == 0.4
    )

    with pytest.raises(MetricNormalizationError, match="duplicate"):
        MetricNormalizationProfile(
            profile_id="broken",
            version="1",
            policies=(higher, higher),
        )
