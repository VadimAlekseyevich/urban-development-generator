from __future__ import annotations

import math

import pytest

from core.urban_generator.demography import (
    AgeGroupAllocationError,
    AgeGroupAllocator,
    AgeGroupShare,
    BuildingPopulationAllocation,
    DemographicScenario,
    PopulationAllocationDiagnostics,
    PopulationAllocationResult,
    PopulationAllocationStatus,
    PopulationTarget,
    PopulationTargetKind,
)


def _scenario(
    *,
    age_groups: tuple[AgeGroupShare, ...] | None = None,
    target_population: int = 10,
) -> DemographicScenario:
    return DemographicScenario(
        version="demography-v1",
        population_target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=target_population,
        ),
        occupancy_ratio=0.9,
        residential_area_per_person_m2=30.0,
        average_household_size=2.5,
        residential_gfa_share=0.6,
        age_groups=age_groups
        or (
            AgeGroupShare(code="0-17", min_age=0, max_age=17, share=0.2),
            AgeGroupShare(code="18+", min_age=18, max_age=None, share=0.8),
        ),
        working_population_ratio=0.6,
    )


def _population_result(
    scenario: DemographicScenario,
    residents: tuple[tuple[str, int], ...],
) -> PopulationAllocationResult:
    ordered = tuple(sorted(residents))
    allocations = tuple(
        BuildingPopulationAllocation(
            building_id=building_id,
            resident_capacity=float(count),
            integer_capacity=count,
            residents=count,
            utilization_ratio=1.0 if count > 0 else 0.0,
        )
        for building_id, count in ordered
    )
    total = sum(count for _, count in ordered)
    return PopulationAllocationResult(
        scenario_version=scenario.version,
        scenario_fingerprint=scenario.fingerprint,
        allocations=allocations,
        diagnostics=PopulationAllocationDiagnostics(
            status=PopulationAllocationStatus.TARGET_MET,
            target_total_population=total,
            baseline_population=0,
            generated_population_target=total,
            allocatable_generated_capacity=total,
            allocated_generated_population=total,
            unmet_generated_population=0,
            baseline_excess_population=0,
        ),
    )


def test_age_allocation_preserves_building_and_cohort_totals() -> None:
    scenario = _scenario()
    population = _population_result(
        scenario,
        (("building:b", 7), ("building:a", 3)),
    )

    result = AgeGroupAllocator().allocate(population, scenario=scenario)

    assert result.total_population == 10
    assert [item.residents for item in result.totals] == [2, 8]
    assert [item.total_residents for item in result.buildings] == [3, 7]
    assert [
        [group.residents for group in building.age_groups]
        for building in result.buildings
    ] == [[1, 2], [1, 6]]
    assert sum(item.residents for item in result.totals) == 10
    assert all(
        sum(group.residents for group in building.age_groups)
        == building.total_residents
        for building in result.buildings
    )


def test_rounding_ties_follow_canonical_age_group_order() -> None:
    groups = (
        AgeGroupShare(code="0-6", min_age=0, max_age=6, share=0.25),
        AgeGroupShare(code="7-17", min_age=7, max_age=17, share=0.25),
        AgeGroupShare(code="18-64", min_age=18, max_age=64, share=0.25),
        AgeGroupShare(code="65+", min_age=65, max_age=None, share=0.25),
    )
    scenario = _scenario(age_groups=groups, target_population=1)
    population = _population_result(scenario, (("building:1", 1),))

    result = AgeGroupAllocator().allocate(population, scenario=scenario)

    assert [item.residents for item in result.totals] == [1, 0, 0, 0]
    assert [
        group.residents for group in result.buildings[0].age_groups
    ] == [1, 0, 0, 0]


def test_zero_population_has_zero_finite_achieved_shares() -> None:
    scenario = _scenario(target_population=0)
    population = _population_result(scenario, (("building:1", 0),))

    result = AgeGroupAllocator().allocate(population, scenario=scenario)

    assert result.total_population == 0
    assert all(item.residents == 0 for item in result.totals)
    assert all(item.achieved_share == 0.0 for item in result.totals)
    assert all(math.isfinite(item.achieved_share) for item in result.totals)


def test_allocation_is_deterministic() -> None:
    scenario = _scenario()
    population = _population_result(
        scenario,
        (
            ("building:c", 2),
            ("building:a", 5),
            ("building:b", 3),
        ),
    )
    allocator = AgeGroupAllocator()

    first = allocator.allocate(population, scenario=scenario)
    second = allocator.allocate(population, scenario=scenario)

    assert first == second


def test_allocator_rejects_population_from_different_scenario() -> None:
    first = _scenario(target_population=10)
    second = _scenario(target_population=11)
    population = _population_result(first, (("building:1", 10),))

    with pytest.raises(AgeGroupAllocationError, match="fingerprint"):
        AgeGroupAllocator().allocate(population, scenario=second)


def test_allocator_bounds_buildings() -> None:
    scenario = _scenario()
    population = _population_result(
        scenario,
        (("building:1", 5), ("building:2", 5)),
    )

    with pytest.raises(AgeGroupAllocationError, match="building limit"):
        AgeGroupAllocator(max_buildings=1).allocate(
            population,
            scenario=scenario,
        )


def test_allocator_bounds_age_groups() -> None:
    scenario = _scenario()
    population = _population_result(scenario, (("building:1", 10),))

    with pytest.raises(AgeGroupAllocationError, match="age group limit"):
        AgeGroupAllocator(max_age_groups=1).allocate(
            population,
            scenario=scenario,
        )
