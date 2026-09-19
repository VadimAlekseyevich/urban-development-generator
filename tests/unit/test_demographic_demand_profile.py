from __future__ import annotations

import math

import pytest

from core.urban_generator.demography.age_allocation import AgeGroupPopulation
from core.urban_generator.demography.aggregation import (
    BlockDemographicAggregate,
    DemographicAggregationResult,
    DemographicAggregationTotals,
    ZoneDemographicAggregate,
)
from core.urban_generator.demography.config import (
    AgeGroupShare,
    DemographicScenario,
    PopulationTarget,
    PopulationTargetKind,
)
from core.urban_generator.demography.demand_profile import (
    DemographicDemandCategory,
    DemographicDemandProfileBuilder,
    DemographicDemandProfileError,
    DemographicDemandSignal,
    DemographicDemandUnit,
)
from core.urban_generator.zoning import ZoneClass


def _scenario(*, working_population_ratio: float = 0.6) -> DemographicScenario:
    return DemographicScenario(
        version="demography-v1",
        population_target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=10,
        ),
        occupancy_ratio=0.9,
        residential_area_per_person_m2=30.0,
        average_household_size=2.5,
        residential_gfa_share=0.6,
        age_groups=(
            AgeGroupShare(code="child", min_age=0, max_age=17, share=0.3),
            AgeGroupShare(code="adult", min_age=18, max_age=None, share=0.7),
        ),
        working_population_ratio=working_population_ratio,
    )


def _groups(child: int, adult: int) -> tuple[AgeGroupPopulation, ...]:
    return (
        AgeGroupPopulation(
            code="child",
            min_age=0,
            max_age=17,
            residents=child,
        ),
        AgeGroupPopulation(
            code="adult",
            min_age=18,
            max_age=None,
            residents=adult,
        ),
    )


def _aggregation(scenario: DemographicScenario) -> DemographicAggregationResult:
    blocks = (
        BlockDemographicAggregate(
            block_id="block-1",
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
            building_count=2,
            population=4,
            age_groups=_groups(1, 3),
            jobs_estimate=1.5,
        ),
        BlockDemographicAggregate(
            block_id="block-2",
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
            building_count=3,
            population=6,
            age_groups=_groups(2, 4),
            jobs_estimate=2.5,
        ),
    )
    return DemographicAggregationResult(
        scenario_version=scenario.version,
        scenario_fingerprint=scenario.fingerprint,
        employment_config_version="employment-v1",
        employment_config_fingerprint="b" * 64,
        blocks=blocks,
        zones=(
            ZoneDemographicAggregate(
                zone_id="zone-1",
                zone_class=ZoneClass.RESIDENTIAL,
                block_count=2,
                building_count=5,
                population=10,
                age_groups=_groups(3, 7),
                jobs_estimate=4.0,
            ),
        ),
        totals=DemographicAggregationTotals(
            building_count=5,
            block_count=2,
            zone_count=1,
            population=10,
            age_groups=_groups(3, 7),
            jobs_estimate=4.0,
        ),
    )


def test_profile_exposes_typed_block_signals_and_exact_totals() -> None:
    scenario = _scenario()
    profile = DemographicDemandProfileBuilder().build(
        _aggregation(scenario),
        scenario=scenario,
    )

    first = profile.blocks[0]
    assert first.block_id == "block-1"
    assert first.signal(DemographicDemandCategory.POPULATION).value == 4.0
    assert first.signal(
        DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
    ).value == 1.0
    assert first.signal(DemographicDemandCategory.WORKFORCE).value == 2.4
    assert first.signal(DemographicDemandCategory.JOBS).value == 1.5

    assert profile.totals.signal(DemographicDemandCategory.POPULATION).value == 10.0
    assert profile.totals.signal(DemographicDemandCategory.WORKFORCE).value == 6.0
    assert profile.totals.signal(DemographicDemandCategory.JOBS).value == 4.0
    assert profile.totals.signal(
        DemographicDemandCategory.AGE_GROUP,
        demographic_group="adult",
    ).value == 7.0


def test_signal_units_and_age_metadata_are_explicit() -> None:
    scenario = _scenario()
    profile = DemographicDemandProfileBuilder().build(
        _aggregation(scenario),
        scenario=scenario,
    )

    child = profile.blocks[0].signal(
        DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
    )
    jobs = profile.blocks[0].signal(DemographicDemandCategory.JOBS)

    assert child.unit is DemographicDemandUnit.PEOPLE
    assert child.min_age == 0
    assert child.max_age == 17
    assert jobs.unit is DemographicDemandUnit.JOBS
    assert jobs.demographic_group is None


def test_workforce_uses_scenario_ratio_without_rounding() -> None:
    scenario = _scenario(working_population_ratio=0.35)
    profile = DemographicDemandProfileBuilder().build(
        _aggregation(scenario),
        scenario=scenario,
    )

    assert profile.blocks[0].signal(
        DemographicDemandCategory.WORKFORCE
    ).value == pytest.approx(1.4)
    assert profile.totals.signal(
        DemographicDemandCategory.WORKFORCE
    ).value == pytest.approx(3.5)


def test_profile_is_deterministic_for_same_inputs() -> None:
    scenario = _scenario()
    builder = DemographicDemandProfileBuilder()
    aggregation = _aggregation(scenario)

    assert builder.build(aggregation, scenario=scenario) == builder.build(
        aggregation,
        scenario=scenario,
    )


def test_scenario_provenance_must_match_aggregation() -> None:
    scenario = _scenario()
    other = DemographicScenario(
        version=scenario.version,
        population_target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=11,
        ),
        occupancy_ratio=scenario.occupancy_ratio,
        residential_area_per_person_m2=scenario.residential_area_per_person_m2,
        average_household_size=scenario.average_household_size,
        residential_gfa_share=scenario.residential_gfa_share,
        age_groups=scenario.age_groups,
        working_population_ratio=scenario.working_population_ratio,
    )

    with pytest.raises(DemographicDemandProfileError, match="fingerprint"):
        DemographicDemandProfileBuilder().build(
            _aggregation(scenario),
            scenario=other,
        )


def test_age_group_metadata_must_match_scenario() -> None:
    scenario = _scenario()
    aggregation = _aggregation(scenario)
    bad_blocks = (
        BlockDemographicAggregate(
            block_id="block-1",
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
            building_count=2,
            population=4,
            age_groups=(
                AgeGroupPopulation(
                    code="minor",
                    min_age=0,
                    max_age=17,
                    residents=1,
                ),
                AgeGroupPopulation(
                    code="adult",
                    min_age=18,
                    max_age=None,
                    residents=3,
                ),
            ),
            jobs_estimate=1.5,
        ),
        aggregation.blocks[1],
    )
    bad = DemographicAggregationResult(
        scenario_version=aggregation.scenario_version,
        scenario_fingerprint=aggregation.scenario_fingerprint,
        employment_config_version=aggregation.employment_config_version,
        employment_config_fingerprint=aggregation.employment_config_fingerprint,
        blocks=bad_blocks,
        zones=aggregation.zones,
        totals=DemographicAggregationTotals(
            building_count=5,
            block_count=2,
            zone_count=1,
            population=10,
            age_groups=(
                AgeGroupPopulation(
                    code="minor",
                    min_age=0,
                    max_age=17,
                    residents=3,
                ),
                AgeGroupPopulation(
                    code="adult",
                    min_age=18,
                    max_age=None,
                    residents=7,
                ),
            ),
            jobs_estimate=4.0,
        ),
    )

    with pytest.raises(DemographicDemandProfileError, match="age-group metadata"):
        DemographicDemandProfileBuilder().build(bad, scenario=scenario)


def test_profile_enforces_block_limit() -> None:
    scenario = _scenario()

    with pytest.raises(DemographicDemandProfileError, match="limit exceeded"):
        DemographicDemandProfileBuilder(max_blocks=1).build(
            _aggregation(scenario),
            scenario=scenario,
        )


@pytest.mark.parametrize("value", [math.inf, math.nan, -1.0])
def test_signal_rejects_invalid_numeric_values(value: float) -> None:
    with pytest.raises(DemographicDemandProfileError):
        DemographicDemandSignal(
            category=DemographicDemandCategory.POPULATION,
            value=value,
            unit=DemographicDemandUnit.PEOPLE,
        )


def test_non_age_signal_cannot_carry_demographic_group_metadata() -> None:
    with pytest.raises(DemographicDemandProfileError, match="only age-group"):
        DemographicDemandSignal(
            category=DemographicDemandCategory.POPULATION,
            value=1.0,
            unit=DemographicDemandUnit.PEOPLE,
            demographic_group="child",
        )
