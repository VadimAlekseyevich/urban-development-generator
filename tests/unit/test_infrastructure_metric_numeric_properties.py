from __future__ import annotations

from itertools import permutations

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
    InfrastructureMetricsError,
    InfrastructurePlacementDemandState,
    InfrastructureType,
    UnmetDemandDiagnostics,
    UnmetDemandResult,
)
from core.urban_generator.infrastructure.metrics import (
    _DistanceSample,
    _weighted_percentile,
)
from core.urban_generator.zoning import ZoneClass

TYPE_CODE = "school.general"
SNAPSHOT_ID = "roads:metric-properties-v1"


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


def _single_case(
    *,
    population: float,
    served: float,
    existing_capacity: float,
) -> tuple[
    UnmetDemandResult,
    InfrastructureGreedyPlacementState,
    InfrastructureAccessibilityBatchResult,
]:
    gross = population * 0.1
    unmet = max(gross - served, 0.0)
    demand = BlockInfrastructureDemand(
        block_id="block-a",
        zone_id="zone-a",
        zone_class=ZoneClass.PUBLIC,
        infrastructure_type_code=TYPE_CODE,
        infrastructure_category=InfrastructureCategory.EDUCATION,
        demographic_signal=DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
        source_signal_value=population,
        demand_rate=0.1,
        gross_demand=gross,
        served_demand=served,
        unmet_demand=unmet,
    )
    result = UnmetDemandResult(
        scenario_version="scenario-v1",
        scenario_fingerprint="a" * 64,
        demands=(demand,),
        summaries=(
            InfrastructureDemandSummary(
                infrastructure_type_code=TYPE_CODE,
                infrastructure_category=InfrastructureCategory.EDUCATION,
                demographic_signal=DemographicDemandCategory.AGE_GROUP,
                demographic_group="child",
                gross_demand=gross,
                served_demand=served,
                unmet_demand=unmet,
                existing_capacity=existing_capacity,
            ),
        ),
        diagnostics=UnmetDemandDiagnostics(
            block_count=1,
            infrastructure_type_count=1,
            demand_item_count=1,
            served_assignment_count=1 if served > 0.0 else 0,
            existing_facility_count=1 if existing_capacity > 0.0 else 0,
        ),
    )
    demand_ref = InfrastructureDemandRef(
        block_id="block-a",
        infrastructure_type_code=TYPE_CODE,
    )
    placement = InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(
            InfrastructurePlacementDemandState(
                demand_ref=demand_ref,
                initial_demand=unmet,
                remaining_demand=unmet,
            ),
        ),
        accepted_facilities=(),
        coverage_cache=(),
        candidate_order=(),
    )
    if served > 0.0:
        reachable = (
            InfrastructureAccessibilityResult(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                demand_ref=demand_ref,
                facility_site_ref=ExistingInfrastructureFacilityRef(
                    facility_id="facility-a",
                    infrastructure_type_code=TYPE_CODE,
                ),
                demand_node=NetworkNodeRef("demand-node"),
                facility_site_node=NetworkNodeRef("facility-node"),
                max_network_distance_m=1_000.0,
                distance_m=125.0,
            ),
        )
        unavailable = ()
    else:
        reachable = ()
        unavailable = (
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
        )
    accessibility = InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.EXISTING_FACILITY,
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        reachable=reachable,
        unavailable=unavailable,
        diagnostics=InfrastructureAccessibilityDiagnostics(
            subject_count=1,
            reachable_count=len(reachable),
            unavailable_count=len(unavailable),
            demand_unsnapped_count=0,
            facility_site_unsnapped_count=0,
            both_unsnapped_count=0,
            no_path_within_max_distance_count=0,
            no_snapped_facility_site_count=len(unavailable),
        ),
    )
    return result, placement, accessibility


@pytest.mark.parametrize(
    ("population", "served"),
    (
        (10.0, 0.0),
        (10.0, 0.25),
        (10.0, 0.5),
        (10.0, 1.0),
        (100.0, 3.0),
        (100.0, 10.0),
    ),
)
def test_population_and_age_coverage_remain_inside_unit_interval(
    population: float,
    served: float,
) -> None:
    unmet, placement, accessibility = _single_case(
        population=population,
        served=served,
        existing_capacity=max(served, 1.0),
    )

    result = InfrastructureMetricsBuilder().build(
        unmet,
        infrastructure_types=(_type(),),
        placements=(placement,),
        existing_accessibility=(accessibility,),
    )

    population_ratio = result.require(
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
    ).scalar_value
    assert population_ratio is not None
    assert 0.0 <= population_ratio <= 1.0

    age = result.require(
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE
    ).age_coverage
    assert len(age) == 1
    assert 0.0 <= age[0].coverage_ratio <= 1.0
    assert age[0].coverage_ratio == pytest.approx(population_ratio)


def test_zero_population_age_cohort_is_finite_and_zero_covered() -> None:
    unmet, placement, accessibility = _single_case(
        population=0.0,
        served=0.0,
        existing_capacity=0.0,
    )

    result = InfrastructureMetricsBuilder().build(
        unmet,
        infrastructure_types=(_type(),),
        placements=(placement,),
        existing_accessibility=(accessibility,),
    )

    age = result.require(
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE
    ).age_coverage
    assert len(age) == 1
    assert age[0].population == 0.0
    assert age[0].covered_population == 0.0
    assert age[0].coverage_ratio == 0.0


def test_weighted_percentiles_are_order_invariant_and_monotone() -> None:
    samples = (
        _DistanceSample(distance_m=50.0, served_demand=1.0),
        _DistanceSample(distance_m=100.0, served_demand=2.0),
        _DistanceSample(distance_m=400.0, served_demand=5.0),
        _DistanceSample(distance_m=900.0, served_demand=2.0),
    )
    expected = (
        _weighted_percentile(list(samples), 0.50),
        _weighted_percentile(list(samples), 0.90),
    )

    assert 50.0 <= expected[0] <= expected[1] <= 900.0
    for order in permutations(samples):
        candidate = (
            _weighted_percentile(list(order), 0.50),
            _weighted_percentile(list(order), 0.90),
        )
        assert candidate == expected


def test_positive_served_demand_requires_positive_total_capacity() -> None:
    unmet, placement, accessibility = _single_case(
        population=100.0,
        served=2.0,
        existing_capacity=0.0,
    )

    with pytest.raises(
        InfrastructureMetricsError,
        match="served demand requires positive",
    ):
        InfrastructureMetricsBuilder().build(
            unmet,
            infrastructure_types=(_type(),),
            placements=(placement,),
            existing_accessibility=(accessibility,),
        )


@pytest.mark.parametrize(
    ("served", "capacity"),
    ((0.0, 0.0), (1.0, 1.0), (2.0, 4.0), (5.0, 10.0)),
)
def test_capacity_utilization_is_finite_and_bounded(
    served: float,
    capacity: float,
) -> None:
    population = max(served * 10.0, 10.0)
    unmet, placement, accessibility = _single_case(
        population=population,
        served=served,
        existing_capacity=capacity,
    )

    result = InfrastructureMetricsBuilder().build(
        unmet,
        infrastructure_types=(_type(),),
        placements=(placement,),
        existing_accessibility=(accessibility,),
    )

    utilization = result.require(
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION
    ).scalar_value
    assert utilization is not None
    assert 0.0 <= utilization <= 1.0
