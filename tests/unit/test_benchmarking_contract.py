import pytest

from core.urban_generator.domain import (
    CANONICAL_DIAGNOSTIC_IDS,
    CANONICAL_RAW_METRIC_DEFINITIONS,
    CANONICAL_RAW_METRIC_IDS,
    V1_EXPERIMENT_DEFINITIONS,
    V1_REFERENCE_PROFILE,
    BaselineId,
    BenchmarkContractError,
    BenchmarkReferenceProfile,
    DiagnosticId,
    ExperimentId,
    MetricDefinition,
    MetricValueKind,
    RawMetricId,
)


def test_canonical_metric_catalog_has_stable_unique_ids() -> None:
    metric_ids = tuple(definition.metric_id for definition in CANONICAL_RAW_METRIC_DEFINITIONS)

    assert metric_ids == CANONICAL_RAW_METRIC_IDS
    assert len(metric_ids) == len(set(metric_ids))
    assert RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT in metric_ids
    assert RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M in metric_ids


def test_distribution_metrics_are_explicitly_typed() -> None:
    by_id = {definition.metric_id: definition for definition in CANONICAL_RAW_METRIC_DEFINITIONS}

    assert (
        by_id[RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION].value_kind
        is MetricValueKind.DISTRIBUTION
    )
    assert (
        by_id[RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION].value_kind
        is MetricValueKind.DISTRIBUTION
    )


def test_v1_reference_profile_requires_raw_metrics_and_diagnostics() -> None:
    assert V1_REFERENCE_PROFILE.required_metrics == CANONICAL_RAW_METRIC_IDS
    assert V1_REFERENCE_PROFILE.required_diagnostics == CANONICAL_DIAGNOSTIC_IDS
    assert DiagnosticId.STAGE_DURATION_SECONDS in V1_REFERENCE_PROFILE.required_diagnostics
    assert DiagnosticId.PEAK_MEMORY_BYTES in V1_REFERENCE_PROFILE.required_diagnostics


def test_v1_experiment_catalog_matches_research_plan() -> None:
    by_id = {definition.experiment_id: definition for definition in V1_EXPERIMENT_DEFINITIONS}

    assert set(V1_REFERENCE_PROFILE.required_experiments) == {
        ExperimentId.E1_REPRODUCIBILITY,
        ExperimentId.E2_SEEDS,
        ExperimentId.E3_DENSITY,
        ExperimentId.E4_CONSTRAINTS_ABLATION,
        ExperimentId.E5_INFRASTRUCTURE_PLACEMENT,
        ExperimentId.E6_SCORE_SENSITIVITY,
    }
    assert V1_REFERENCE_PROFILE.optional_experiments == (
        ExperimentId.E7_REAL_GROWTH_HOLDOUT,
    )
    assert by_id[ExperimentId.E7_REAL_GROWTH_HOLDOUT].optional is True
    assert by_id[ExperimentId.E4_CONSTRAINTS_ABLATION].baselines == (
        BaselineId.CONSTRAINTS_DISABLED_OR_SOFTENED,
    )
    assert by_id[ExperimentId.E5_INFRASTRUCTURE_PLACEMENT].baselines == (
        BaselineId.RANDOM_INFRASTRUCTURE,
    )


def test_reference_profile_rejects_duplicate_or_overlapping_experiments() -> None:
    with pytest.raises(BenchmarkContractError, match="must not contain duplicates"):
        BenchmarkReferenceProfile(
            profile_id="broken",
            version="1",
            required_metrics=(
                RawMetricId.LAND_DEVELOPABLE_AREA_M2,
                RawMetricId.LAND_DEVELOPABLE_AREA_M2,
            ),
            required_diagnostics=(),
            required_experiments=(ExperimentId.E1_REPRODUCIBILITY,),
        )

    with pytest.raises(BenchmarkContractError, match="must not overlap"):
        BenchmarkReferenceProfile(
            profile_id="broken",
            version="1",
            required_metrics=(),
            required_diagnostics=(),
            required_experiments=(ExperimentId.E1_REPRODUCIBILITY,),
            optional_experiments=(ExperimentId.E1_REPRODUCIBILITY,),
        )


def test_metric_definition_requires_unit() -> None:
    with pytest.raises(BenchmarkContractError, match="unit must be non-empty"):
        MetricDefinition(
            metric_id=RawMetricId.LAND_DEVELOPABLE_AREA_M2,
            unit=" ",
        )
