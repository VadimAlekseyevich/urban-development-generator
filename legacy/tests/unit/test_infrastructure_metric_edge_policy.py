import pytest

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import RawMetricId
from core.urban_generator.infrastructure import (
    BlockInfrastructureDemand,
    InfrastructureAccessibilityBatchResult,
    InfrastructureAccessibilityDiagnostics,
    InfrastructureAccessibilityMode,
    InfrastructureAccessibilityUnavailable,
    InfrastructureAccessibilityUnavailableReason,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureDemandSummary,
    InfrastructureGreedyPlacementState,
    InfrastructureMetricsBuilder,
    InfrastructurePlacementDemandState,
    InfrastructureType,
    UnmetDemandDiagnostics,
    UnmetDemandResult,
)
from core.urban_generator.zoning import ZoneClass

TYPE_CODE = "school.general"
SNAPSHOT_ID = "roads:edge-policy-v1"


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
            sources=(InfrastructureCandidateSource.PARCEL,),
        ),
    )


def _unreachable_demand() -> BlockInfrastructureDemand:
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
        served_demand=0.0,
        unmet_demand=10.0,
    )


def _unreachable_result() -> UnmetDemandResult:
    return UnmetDemandResult(
        scenario_version="scenario-v1",
        scenario_fingerprint="a" * 64,
        demands=(_unreachable_demand(),),
        summaries=(
            InfrastructureDemandSummary(
                infrastructure_type_code=TYPE_CODE,
                infrastructure_category=InfrastructureCategory.EDUCATION,
                demographic_signal=DemographicDemandCategory.AGE_GROUP,
                demographic_group="child",
                gross_demand=10.0,
                served_demand=0.0,
                unmet_demand=10.0,
                existing_capacity=0.0,
            ),
        ),
        diagnostics=UnmetDemandDiagnostics(
            block_count=1,
            infrastructure_type_count=1,
            demand_item_count=1,
            served_assignment_count=0,
            existing_facility_count=0,
        ),
    )


def _unreachable_accessibility() -> InfrastructureAccessibilityBatchResult:
    demand_ref = InfrastructureDemandRef(
        block_id="block-a",
        infrastructure_type_code=TYPE_CODE,
    )
    return InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.EXISTING_FACILITY,
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        reachable=(),
        unavailable=(
            InfrastructureAccessibilityUnavailable(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                demand_ref=demand_ref,
                facility_site_ref=None,
                max_network_distance_m=1_000.0,
                reason=(
                    InfrastructureAccessibilityUnavailableReason.NO_SNAPPED_FACILITY_SITE
                ),
            ),
        ),
        diagnostics=InfrastructureAccessibilityDiagnostics(
            subject_count=1,
            reachable_count=0,
            unavailable_count=1,
            demand_unsnapped_count=0,
            facility_site_unsnapped_count=0,
            both_unsnapped_count=0,
            no_path_within_max_distance_count=0,
            no_snapped_facility_site_count=1,
        ),
    )


def _unreachable_placement() -> InfrastructureGreedyPlacementState:
    demand_ref = InfrastructureDemandRef(
        block_id="block-a",
        infrastructure_type_code=TYPE_CODE,
    )
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(
            InfrastructurePlacementDemandState(
                demand_ref=demand_ref,
                initial_demand=10.0,
                remaining_demand=10.0,
            ),
        ),
        accepted_facilities=(),
        coverage_cache=(),
        candidate_order=(),
    )


def test_empty_project_has_zero_totals_and_missing_distance_percentiles() -> None:
    result = InfrastructureMetricsBuilder().build(
        UnmetDemandResult(
            scenario_version="scenario-v1",
            scenario_fingerprint="a" * 64,
            demands=(),
            summaries=(),
            diagnostics=UnmetDemandDiagnostics(
                block_count=0,
                infrastructure_type_count=0,
                demand_item_count=0,
                served_assignment_count=0,
                existing_facility_count=0,
            ),
        ),
        infrastructure_types=(),
        placements=(),
        existing_accessibility=(),
    )

    assert result.require(
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
    ).scalar_value == 0.0
    assert result.require(
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE
    ).age_coverage == ()
    assert result.require(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M
    ).scalar_value is None
    assert result.require(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M
    ).scalar_value is None
    assert result.require(
        RawMetricId.INFRASTRUCTURE_UNMET_DEMAND
    ).scalar_value == 0.0
    assert result.require(
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION
    ).scalar_value == 0.0


def test_unreachable_only_demand_stays_unmet_without_fake_distance() -> None:
    result = InfrastructureMetricsBuilder().build(
        _unreachable_result(),
        infrastructure_types=(_type(),),
        placements=(_unreachable_placement(),),
        existing_accessibility=(_unreachable_accessibility(),),
    )

    assert result.require(
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
    ).scalar_value == 0.0
    age = result.require(
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE
    ).age_coverage
    assert len(age) == 1
    assert age[0].coverage_ratio == 0.0
    assert result.require(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M
    ).scalar_value is None
    assert result.require(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M
    ).scalar_value is None
    assert result.require(
        RawMetricId.INFRASTRUCTURE_UNMET_DEMAND
    ).scalar_value == pytest.approx(10.0)
    assert result.require(
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION
    ).scalar_value == 0.0
