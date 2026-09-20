import pytest

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import NetworkNodeRef, RawMetricId
from core.urban_generator.infrastructure import (
    BlockInfrastructureDemand,
    ExistingInfrastructureFacilityRef,
    InfrastructureAccessibilityBatchResult,
    InfrastructureAccessibilityDiagnostics,
    InfrastructureAccessibilityMode,
    InfrastructureAccessibilityResult,
    InfrastructureAcceptedFacility,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCategory,
    InfrastructureCoverageCacheEntry,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureDemandSummary,
    InfrastructureGreedyPlacementState,
    InfrastructureMetricsBuilder,
    InfrastructureMetricsError,
    InfrastructurePlacementDemandState,
    InfrastructureType,
    UnmetDemandDiagnostics,
    UnmetDemandResult,
)
from core.urban_generator.zoning import ZoneClass

TYPE_CODE = "school.general"
SNAPSHOT_ID = "roads:metrics-v1"


def _type() -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=0.1,
        ),
        capacity=5.0,
        max_network_distance_m=1_000.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=100.0,
        target_site_area_m2=200.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(),
        ),
    )


def _demand() -> BlockInfrastructureDemand:
    return BlockInfrastructureDemand(
        block_id="block-a",
        zone_id="zone-a",
        zone_class=ZoneClass.PUBLIC,
        infrastructure_type_code=TYPE_CODE,
        infrastructure_category=InfrastructureCategory.EDUCATION,
        demographic_signal=DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
        source_signal_value=100.0,
        demand_rate=0.1,
        gross_demand=10.0,
        served_demand=2.0,
        unmet_demand=8.0,
    )


def _unmet_result() -> UnmetDemandResult:
    return UnmetDemandResult(
        scenario_version="scenario-v1",
        scenario_fingerprint="a" * 64,
        demands=(_demand(),),
        summaries=(
            InfrastructureDemandSummary(
                infrastructure_type_code=TYPE_CODE,
                infrastructure_category=InfrastructureCategory.EDUCATION,
                demographic_signal=DemographicDemandCategory.AGE_GROUP,
                demographic_group="child",
                gross_demand=10.0,
                served_demand=2.0,
                unmet_demand=8.0,
                existing_capacity=4.0,
            ),
        ),
        diagnostics=UnmetDemandDiagnostics(
            block_count=1,
            infrastructure_type_count=1,
            demand_item_count=1,
            served_assignment_count=1,
            existing_facility_count=1,
        ),
    )


def _existing_accessibility(
    *,
    snapshot_id: str = SNAPSHOT_ID,
) -> InfrastructureAccessibilityBatchResult:
    demand_ref = InfrastructureDemandRef(
        block_id="block-a",
        infrastructure_type_code=TYPE_CODE,
    )
    return InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.EXISTING_FACILITY,
        snapshot_id=snapshot_id,
        infrastructure_type_code=TYPE_CODE,
        reachable=(
            InfrastructureAccessibilityResult(
                snapshot_id=snapshot_id,
                infrastructure_type_code=TYPE_CODE,
                demand_ref=demand_ref,
                facility_site_ref=ExistingInfrastructureFacilityRef(
                    facility_id="facility-a",
                    infrastructure_type_code=TYPE_CODE,
                ),
                demand_node=NetworkNodeRef("demand-node"),
                facility_site_node=NetworkNodeRef("existing-node"),
                max_network_distance_m=1_000.0,
                distance_m=100.0,
            ),
        ),
        unavailable=(),
        diagnostics=InfrastructureAccessibilityDiagnostics(
            subject_count=1,
            reachable_count=1,
            unavailable_count=0,
            demand_unsnapped_count=0,
            facility_site_unsnapped_count=0,
            both_unsnapped_count=0,
            no_path_within_max_distance_count=0,
            no_snapped_facility_site_count=0,
        ),
    )


def _placement() -> InfrastructureGreedyPlacementState:
    demand_ref = InfrastructureDemandRef(
        block_id="block-a",
        infrastructure_type_code=TYPE_CODE,
    )
    candidate_ref = InfrastructureCandidateRef(
        candidate_id="candidate-a",
        infrastructure_type_code=TYPE_CODE,
    )
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(
            InfrastructurePlacementDemandState(
                demand_ref=demand_ref,
                initial_demand=8.0,
                remaining_demand=3.0,
            ),
        ),
        accepted_facilities=(
            InfrastructureAcceptedFacility(
                candidate_ref=candidate_ref,
                acceptance_index=0,
            ),
        ),
        coverage_cache=(
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                candidate_ref=candidate_ref,
                accessibility=(
                    InfrastructureAccessibilityResult(
                        snapshot_id=SNAPSHOT_ID,
                        infrastructure_type_code=TYPE_CODE,
                        demand_ref=demand_ref,
                        facility_site_ref=candidate_ref,
                        demand_node=NetworkNodeRef("demand-node"),
                        facility_site_node=NetworkNodeRef("candidate-node"),
                        max_network_distance_m=1_000.0,
                        distance_m=300.0,
                    ),
                ),
            ),
        ),
        candidate_order=(candidate_ref,),
    )


def test_builder_computes_all_canonical_infrastructure_raw_metrics() -> None:
    result = InfrastructureMetricsBuilder().build(
        _unmet_result(),
        infrastructure_types=(_type(),),
        placements=(_placement(),),
        existing_accessibility=(_existing_accessibility(),),
    )

    assert tuple(item.metric_id for item in result.raw_metrics) == (
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
        RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
    )
    assert result.require(
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
    ).scalar_value == pytest.approx(0.7)

    age_metric = result.require(
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE
    )
    assert age_metric.scalar_value is None
    assert len(age_metric.age_coverage) == 1
    assert age_metric.age_coverage[0].demographic_group == "child"
    assert age_metric.age_coverage[0].population == pytest.approx(100.0)
    assert age_metric.age_coverage[0].covered_population == pytest.approx(70.0)
    assert age_metric.age_coverage[0].coverage_ratio == pytest.approx(0.7)

    assert result.require(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M
    ).scalar_value == pytest.approx(300.0)
    assert result.require(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M
    ).scalar_value == pytest.approx(300.0)
    assert result.require(
        RawMetricId.INFRASTRUCTURE_UNMET_DEMAND
    ).scalar_value == pytest.approx(3.0)
    assert result.require(
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION
    ).scalar_value == pytest.approx(7.0 / 9.0)

    assert result.diagnostics.infrastructure_type_count == 1
    assert result.diagnostics.demand_item_count == 1
    assert result.diagnostics.accepted_generated_facility_count == 1
    assert result.diagnostics.existing_distance_sample_count == 1
    assert result.diagnostics.generated_distance_sample_count == 1


def test_builder_rejects_cross_snapshot_accessibility_without_rerouting() -> None:
    with pytest.raises(
        InfrastructureMetricsError,
        match="snapshots must match",
    ):
        InfrastructureMetricsBuilder().build(
            _unmet_result(),
            infrastructure_types=(_type(),),
            placements=(_placement(),),
            existing_accessibility=(
                _existing_accessibility(snapshot_id="roads:other"),
            ),
        )
