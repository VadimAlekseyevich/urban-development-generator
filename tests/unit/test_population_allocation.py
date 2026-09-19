from __future__ import annotations

import math

import pytest

from core.urban_generator.demography import (
    AgeGroupShare,
    DemographicScenario,
    PopulationAllocationError,
    PopulationAllocationStatus,
    PopulationAllocator,
    PopulationTarget,
    PopulationTargetKind,
    ResidentialBuildingCapacity,
    ResidentialCapacityResult,
)


def _scenario(
    *,
    target: PopulationTarget | None = None,
) -> DemographicScenario:
    return DemographicScenario(
        version="demography-v1",
        population_target=target
        or PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=10,
        ),
        occupancy_ratio=0.9,
        residential_area_per_person_m2=30.0,
        average_household_size=2.5,
        residential_gfa_share=0.6,
        age_groups=(
            AgeGroupShare(code="0-17", min_age=0, max_age=17, share=0.2),
            AgeGroupShare(code="18+", min_age=18, max_age=None, share=0.8),
        ),
        working_population_ratio=0.6,
    )


def _capacity_result(
    scenario: DemographicScenario,
    capacities: tuple[tuple[str, float], ...],
) -> ResidentialCapacityResult:
    return ResidentialCapacityResult(
        scenario_version=scenario.version,
        scenario_fingerprint=scenario.fingerprint,
        buildings=tuple(
            ResidentialBuildingCapacity(
                building_id=building_id,
                use=_residential_use(),
                total_gfa_m2=max(1.0, capacity * 30.0),
                residential_gfa_m2=max(1.0, capacity * 30.0),
                occupied_residential_area_m2=max(0.0, capacity * 30.0),
                resident_capacity=capacity,
                household_capacity=capacity / 2.5,
            )
            for building_id, capacity in sorted(capacities)
        ),
    )


def _residential_use():
    from core.urban_generator.buildings import BuildingUse

    return BuildingUse.RESIDENTIAL


def test_absolute_target_is_apportioned_with_capacity_ceiling() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=5,
        )
    )
    capacities = _capacity_result(
        scenario,
        (("building:b", 4.2), ("building:a", 6.8)),
    )

    result = PopulationAllocator().allocate(capacities, scenario=scenario)

    assert result.diagnostics.status is PopulationAllocationStatus.TARGET_MET
    assert result.diagnostics.target_total_population == 5
    assert result.diagnostics.baseline_population == 0
    assert result.diagnostics.generated_population_target == 5
    assert result.diagnostics.allocated_generated_population == 5
    assert [item.residents for item in result.allocations] == [3, 2]
    assert all(math.isfinite(item.utilization_ratio) for item in result.allocations)


def test_absolute_target_subtracts_existing_baseline_population() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=100,
        )
    )
    capacities = _capacity_result(scenario, (("building:1", 20.0),))

    result = PopulationAllocator().allocate(
        capacities,
        scenario=scenario,
        baseline_population=90,
    )

    assert result.diagnostics.target_total_population == 100
    assert result.diagnostics.generated_population_target == 10
    assert result.diagnostics.allocated_generated_population == 10


def test_growth_target_allocates_only_incremental_population() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.GROWTH_RATE,
            value=0.125,
        )
    )
    capacities = _capacity_result(scenario, (("building:1", 200.0),))

    result = PopulationAllocator().allocate(
        capacities,
        scenario=scenario,
        baseline_population=1_000,
    )

    assert result.diagnostics.target_total_population == 1_125
    assert result.diagnostics.baseline_population == 1_000
    assert result.diagnostics.generated_population_target == 125
    assert result.diagnostics.allocated_generated_population == 125


def test_growth_target_requires_baseline_population() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.GROWTH_RATE,
            value=0.1,
        )
    )
    capacities = _capacity_result(scenario, (("building:1", 20.0),))

    with pytest.raises(PopulationAllocationError, match="requires baseline"):
        PopulationAllocator().allocate(capacities, scenario=scenario)


def test_capacity_shortfall_is_explicit() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=20,
        )
    )
    capacities = _capacity_result(
        scenario,
        (("building:a", 6.9), ("building:b", 4.9)),
    )

    result = PopulationAllocator().allocate(capacities, scenario=scenario)

    assert result.diagnostics.status is PopulationAllocationStatus.CAPACITY_EXHAUSTED
    assert result.diagnostics.allocatable_generated_capacity == 10
    assert result.diagnostics.allocated_generated_population == 10
    assert result.diagnostics.unmet_generated_population == 10
    assert [item.residents for item in result.allocations] == [6, 4]


def test_baseline_above_target_is_not_hidden_by_new_allocation() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=80,
        )
    )
    capacities = _capacity_result(scenario, (("building:1", 50.0),))

    result = PopulationAllocator().allocate(
        capacities,
        scenario=scenario,
        baseline_population=100,
    )

    assert (
        result.diagnostics.status
        is PopulationAllocationStatus.BASELINE_EXCEEDS_TARGET
    )
    assert result.diagnostics.generated_population_target == 0
    assert result.diagnostics.allocated_generated_population == 0
    assert result.diagnostics.baseline_excess_population == 20
    assert result.allocations[0].residents == 0


def test_negative_growth_reports_baseline_excess_without_nan() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.GROWTH_RATE,
            value=-0.1,
        )
    )
    capacities = _capacity_result(scenario, (("building:1", 50.0),))

    result = PopulationAllocator().allocate(
        capacities,
        scenario=scenario,
        baseline_population=1_000,
    )

    assert result.diagnostics.target_total_population == 900
    assert result.diagnostics.baseline_excess_population == 100
    assert (
        result.diagnostics.status
        is PopulationAllocationStatus.BASELINE_EXCEEDS_TARGET
    )
    assert result.allocations[0].utilization_ratio == 0.0


def test_tied_remainders_are_resolved_by_building_id() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=1,
        )
    )
    capacities = _capacity_result(
        scenario,
        (("building:b", 5.0), ("building:a", 5.0)),
    )

    first = PopulationAllocator().allocate(capacities, scenario=scenario)
    second = PopulationAllocator().allocate(capacities, scenario=scenario)

    assert first == second
    assert [item.residents for item in first.allocations] == [1, 0]


def test_fractional_capacity_below_one_never_creates_nan_or_resident() -> None:
    scenario = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=1,
        )
    )
    capacities = _capacity_result(scenario, (("building:1", 0.75),))

    result = PopulationAllocator().allocate(capacities, scenario=scenario)

    allocation = result.allocations[0]
    assert allocation.integer_capacity == 0
    assert allocation.residents == 0
    assert allocation.utilization_ratio == 0.0
    assert math.isfinite(allocation.resident_capacity)
    assert math.isfinite(allocation.utilization_ratio)


def test_allocator_rejects_capacity_from_different_scenario() -> None:
    first = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=10,
        )
    )
    second = _scenario(
        target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=11,
        )
    )
    capacities = _capacity_result(first, (("building:1", 20.0),))

    with pytest.raises(PopulationAllocationError, match="fingerprint"):
        PopulationAllocator().allocate(capacities, scenario=second)


def test_allocator_bounds_building_count() -> None:
    scenario = _scenario()
    capacities = _capacity_result(
        scenario,
        (("building:1", 5.0), ("building:2", 5.0)),
    )

    with pytest.raises(PopulationAllocationError, match="limit exceeded"):
        PopulationAllocator(max_buildings=1).allocate(
            capacities,
            scenario=scenario,
        )
