import pytest

from core.urban_generator.domain import (
    CANONICAL_DIAGNOSTIC_IDS,
    CANONICAL_METRIC_REGISTRY,
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
    MetricDirection,
    MetricRegistry,
    MetricScope,
    MetricSource,
    MetricValueKind,
    RawMetricId,
)

from core.urban_generator.metrics import (
    CONSTRAINT_RAW_METRIC_IDS,
    DEMOGRAPHY_RAW_METRIC_IDS,
    INFRASTRUCTURE_RAW_METRIC_IDS,
    LAND_BUILDING_RAW_METRIC_IDS,
    ROAD_RAW_METRIC_IDS,
)

def _definition(
    metric_id: RawMetricId = RawMetricId.LAND_DEVELOPABLE_AREA_M2,
    *,
    unit: str = "m2",
) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        unit=unit,
        scope=MetricScope.LAND,
        direction=MetricDirection.DESCRIPTIVE,
        source=MetricSource.LAND_BUILDING,
        version="1",
    )


def test_canonical_metric_catalog_has_stable_unique_ids() -> None:
    metric_ids = tuple(
        definition.metric_id
        for definition in CANONICAL_RAW_METRIC_DEFINITIONS
    )

    assert CANONICAL_METRIC_REGISTRY.definitions == CANONICAL_RAW_METRIC_DEFINITIONS
    assert metric_ids == CANONICAL_RAW_METRIC_IDS
    assert metric_ids == tuple(RawMetricId)
    assert len(metric_ids) == len(set(metric_ids))
    assert RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT in metric_ids
    assert RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M in metric_ids


def test_s11_metric_adapter_families_partition_canonical_ids() -> None:
    flattened = (
        LAND_BUILDING_RAW_METRIC_IDS
        + DEMOGRAPHY_RAW_METRIC_IDS
        + ROAD_RAW_METRIC_IDS
        + INFRASTRUCTURE_RAW_METRIC_IDS
        + CONSTRAINT_RAW_METRIC_IDS
    )

    assert flattened == CANONICAL_RAW_METRIC_IDS
    assert len(flattened) == len(set(flattened))


def test_canonical_metric_registry_exposes_runtime_metadata() -> None:
    coverage = CANONICAL_METRIC_REGISTRY.get(
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
    )
    assert coverage.scope is MetricScope.INFRASTRUCTURE
    assert coverage.direction is MetricDirection.HIGHER_IS_BETTER
    assert coverage.source is MetricSource.INFRASTRUCTURE
    assert coverage.version == "1"

    hard_violations = CANONICAL_METRIC_REGISTRY.get(
        RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT
    )
    assert hard_violations.scope is MetricScope.CONSTRAINTS
    assert hard_violations.direction is MetricDirection.LOWER_IS_BETTER
    assert hard_violations.source is MetricSource.CONSTRAINTS


def test_metric_registry_filters_by_scope_and_source_without_new_ids() -> None:
    infrastructure = CANONICAL_METRIC_REGISTRY.definitions_for(
        scope=MetricScope.INFRASTRUCTURE
    )
    by_source = CANONICAL_METRIC_REGISTRY.definitions_for(
        source=MetricSource.INFRASTRUCTURE
    )

    assert infrastructure == by_source
    assert tuple(item.metric_id for item in infrastructure) == (
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
        RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
    )


def test_distribution_metrics_are_explicitly_typed_and_descriptive() -> None:
    by_id = {
        definition.metric_id: definition
        for definition in CANONICAL_RAW_METRIC_DEFINITIONS
    }

    for metric_id in (
        RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION,
        RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION,
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
    ):
        assert by_id[metric_id].value_kind is MetricValueKind.DISTRIBUTION
        assert by_id[metric_id].direction is MetricDirection.DESCRIPTIVE


def test_v1_reference_profile_requires_raw_metrics_and_diagnostics() -> None:
    assert V1_REFERENCE_PROFILE.required_metrics == CANONICAL_RAW_METRIC_IDS
    assert V1_REFERENCE_PROFILE.required_diagnostics == CANONICAL_DIAGNOSTIC_IDS
    assert DiagnosticId.STAGE_DURATION_SECONDS in V1_REFERENCE_PROFILE.required_diagnostics
    assert DiagnosticId.PEAK_MEMORY_BYTES in V1_REFERENCE_PROFILE.required_diagnostics


def test_v1_experiment_catalog_matches_research_plan() -> None:
    by_id = {
        definition.experiment_id: definition
        for definition in V1_EXPERIMENT_DEFINITIONS
    }

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


def test_metric_definition_requires_typed_runtime_metadata() -> None:
    with pytest.raises(BenchmarkContractError, match="unit must be non-empty"):
        _definition(unit=" ")

    with pytest.raises(BenchmarkContractError, match="version"):
        MetricDefinition(
            metric_id=RawMetricId.LAND_DEVELOPABLE_AREA_M2,
            unit="m2",
            scope=MetricScope.LAND,
            direction=MetricDirection.DESCRIPTIVE,
            source=MetricSource.LAND_BUILDING,
            version="bad version",
        )


def test_metric_registry_rejects_duplicates_and_invalid_filters() -> None:
    definition = _definition()

    with pytest.raises(BenchmarkContractError, match="duplicate"):
        MetricRegistry((definition, definition))

    with pytest.raises(BenchmarkContractError, match="scope filter"):
        CANONICAL_METRIC_REGISTRY.definitions_for(scope="LAND")

    with pytest.raises(BenchmarkContractError, match="source filter"):
        CANONICAL_METRIC_REGISTRY.definitions_for(source="LAND_BUILDING")
