from __future__ import annotations

import pytest

from core.urban_generator.buildings import BuildingUse
from core.urban_generator.demography import (
    AgeGroupAllocationResult,
    AgeGroupAllocationTotal,
    AgeGroupPopulation,
    BuildingAgeGroupAllocation,
    BuildingAggregationRef,
    BuildingJobEstimate,
    BuildingPopulationAllocation,
    DemographicAggregationError,
    DemographicAggregator,
    EmploymentEstimateResult,
    EmploymentEstimateSummary,
    PopulationAllocationDiagnostics,
    PopulationAllocationResult,
    PopulationAllocationStatus,
)
from core.urban_generator.zoning import ZoneClass

SCENARIO_FP = "a" * 64
CONFIG_FP = "b" * 64


def _population() -> PopulationAllocationResult:
    allocations = (
        BuildingPopulationAllocation(
            building_id="b-1",
            resident_capacity=3.0,
            integer_capacity=3,
            residents=3,
            utilization_ratio=1.0,
        ),
        BuildingPopulationAllocation(
            building_id="b-2",
            resident_capacity=2.0,
            integer_capacity=2,
            residents=2,
            utilization_ratio=1.0,
        ),
        BuildingPopulationAllocation(
            building_id="b-3",
            resident_capacity=5.0,
            integer_capacity=5,
            residents=5,
            utilization_ratio=1.0,
        ),
    )
    return PopulationAllocationResult(
        scenario_version="demography-v1",
        scenario_fingerprint=SCENARIO_FP,
        allocations=allocations,
        diagnostics=PopulationAllocationDiagnostics(
            status=PopulationAllocationStatus.TARGET_MET,
            target_total_population=10,
            baseline_population=0,
            generated_population_target=10,
            allocatable_generated_capacity=10,
            allocated_generated_population=10,
            unmet_generated_population=0,
            baseline_excess_population=0,
        ),
    )


def _ages() -> AgeGroupAllocationResult:
    buildings = (
        BuildingAgeGroupAllocation(
            building_id="b-1",
            total_residents=3,
            age_groups=(
                AgeGroupPopulation(code="child", min_age=0, max_age=17, residents=1),
                AgeGroupPopulation(code="adult", min_age=18, max_age=None, residents=2),
            ),
        ),
        BuildingAgeGroupAllocation(
            building_id="b-2",
            total_residents=2,
            age_groups=(
                AgeGroupPopulation(code="child", min_age=0, max_age=17, residents=0),
                AgeGroupPopulation(code="adult", min_age=18, max_age=None, residents=2),
            ),
        ),
        BuildingAgeGroupAllocation(
            building_id="b-3",
            total_residents=5,
            age_groups=(
                AgeGroupPopulation(code="child", min_age=0, max_age=17, residents=1),
                AgeGroupPopulation(code="adult", min_age=18, max_age=None, residents=4),
            ),
        ),
    )
    return AgeGroupAllocationResult(
        scenario_version="demography-v1",
        scenario_fingerprint=SCENARIO_FP,
        buildings=buildings,
        totals=(
            AgeGroupAllocationTotal(
                code="child",
                min_age=0,
                max_age=17,
                target_share=0.2,
                residents=2,
                achieved_share=0.2,
            ),
            AgeGroupAllocationTotal(
                code="adult",
                min_age=18,
                max_age=None,
                target_share=0.8,
                residents=8,
                achieved_share=0.8,
            ),
        ),
        total_population=10,
    )


def _employment() -> EmploymentEstimateResult:
    return EmploymentEstimateResult(
        scenario_version="demography-v1",
        scenario_fingerprint=SCENARIO_FP,
        config_version="employment-v1",
        config_fingerprint=CONFIG_FP,
        buildings=(
            BuildingJobEstimate(
                building_id="b-1",
                use=BuildingUse.RESIDENTIAL,
                total_gfa_m2=100.0,
                job_supporting_gfa_m2=0.0,
                area_per_job_m2=None,
                jobs_estimate=0.0,
            ),
            BuildingJobEstimate(
                building_id="b-2",
                use=BuildingUse.COMMERCIAL,
                total_gfa_m2=100.0,
                job_supporting_gfa_m2=100.0,
                area_per_job_m2=50.0,
                jobs_estimate=2.0,
            ),
            BuildingJobEstimate(
                building_id="b-3",
                use=BuildingUse.MIXED,
                total_gfa_m2=100.0,
                job_supporting_gfa_m2=40.0,
                area_per_job_m2=20.0,
                jobs_estimate=2.0,
            ),
        ),
        summary=EmploymentEstimateSummary(
            total_jobs_estimate=4.0,
            generated_population=10,
            working_population_ratio=0.6,
            generated_workforce_estimate=6.0,
        ),
    )


def _refs() -> tuple[BuildingAggregationRef, ...]:
    return (
        BuildingAggregationRef(
            building_id="b-1",
            block_id="block-1",
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
        ),
        BuildingAggregationRef(
            building_id="b-2",
            block_id="block-1",
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
        ),
        BuildingAggregationRef(
            building_id="b-3",
            block_id="block-2",
            zone_id="zone-2",
            zone_class=ZoneClass.MIXED,
        ),
    )


def test_block_and_zone_aggregation_preserves_all_totals() -> None:
    result = DemographicAggregator().aggregate(
        _refs(),
        population=_population(),
        age_groups=_ages(),
        employment=_employment(),
    )

    assert result.totals.population == 10
    assert result.totals.jobs_estimate == 4.0
    assert [item.residents for item in result.totals.age_groups] == [2, 8]

    block_1, block_2 = result.blocks
    assert (block_1.population, block_1.jobs_estimate) == (5, 2.0)
    assert [item.residents for item in block_1.age_groups] == [1, 4]
    assert (block_2.population, block_2.jobs_estimate) == (5, 2.0)
    assert [item.residents for item in block_2.age_groups] == [1, 4]

    zone_1, zone_2 = result.zones
    assert (zone_1.population, zone_1.jobs_estimate) == (5, 2.0)
    assert (zone_2.population, zone_2.jobs_estimate) == (5, 2.0)


def test_aggregation_is_independent_of_ref_order() -> None:
    aggregator = DemographicAggregator()
    first = aggregator.aggregate(
        _refs(),
        population=_population(),
        age_groups=_ages(),
        employment=_employment(),
    )
    second = aggregator.aggregate(
        tuple(reversed(_refs())),
        population=_population(),
        age_groups=_ages(),
        employment=_employment(),
    )

    assert first == second


def test_all_inputs_must_cover_identical_building_ids() -> None:
    with pytest.raises(DemographicAggregationError, match="identical building ids"):
        DemographicAggregator().aggregate(
            _refs()[:-1],
            population=_population(),
            age_groups=_ages(),
            employment=_employment(),
        )


def test_one_block_cannot_belong_to_multiple_zones() -> None:
    refs = (
        _refs()[0],
        BuildingAggregationRef(
            building_id="b-2",
            block_id="block-1",
            zone_id="zone-2",
            zone_class=ZoneClass.MIXED,
        ),
        _refs()[2],
    )

    with pytest.raises(DemographicAggregationError, match="same zone"):
        DemographicAggregator().aggregate(
            refs,
            population=_population(),
            age_groups=_ages(),
            employment=_employment(),
        )


def test_scenario_provenance_must_match() -> None:
    employment = _employment()
    mismatched = EmploymentEstimateResult(
        scenario_version="demography-v2",
        scenario_fingerprint="c" * 64,
        config_version=employment.config_version,
        config_fingerprint=employment.config_fingerprint,
        buildings=employment.buildings,
        summary=employment.summary,
    )

    with pytest.raises(DemographicAggregationError, match="provenance"):
        DemographicAggregator().aggregate(
            _refs(),
            population=_population(),
            age_groups=_ages(),
            employment=mismatched,
        )


def test_aggregation_enforces_building_limit() -> None:
    with pytest.raises(DemographicAggregationError, match="limit exceeded"):
        DemographicAggregator(max_buildings=2).aggregate(
            _refs(),
            population=_population(),
            age_groups=_ages(),
            employment=_employment(),
        )
