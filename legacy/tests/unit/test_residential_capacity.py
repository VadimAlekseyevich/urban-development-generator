from __future__ import annotations

import pytest

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaMetrics,
    BuildingUse,
)
from core.urban_generator.demography import (
    AgeGroupShare,
    DemographicScenario,
    PopulationTarget,
    PopulationTargetKind,
    ResidentialCapacityCalculator,
    ResidentialCapacityError,
    ResidentialCapacitySubject,
)
from core.urban_generator.zoning import ZoneClass


def _scenario(
    *,
    target: PopulationTarget | None = None,
    residential_gfa_share: float = 0.6,
) -> DemographicScenario:
    return DemographicScenario(
        version="demography-v1",
        population_target=target
        or PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=10_000,
        ),
        occupancy_ratio=0.9,
        residential_area_per_person_m2=30.0,
        average_household_size=2.5,
        residential_gfa_share=residential_gfa_share,
        age_groups=(
            AgeGroupShare(code="0-17", min_age=0, max_age=17, share=0.2),
            AgeGroupShare(code="18+", min_age=18, max_age=None, share=0.8),
        ),
        working_population_ratio=0.6,
    )


def _subject(
    building_id: str,
    *,
    use: BuildingUse,
    gfa_m2: float = 1_000.0,
    floors: int = 2,
) -> ResidentialCapacitySubject:
    return ResidentialCapacitySubject(
        metrics=BuildingAreaMetrics(
            building_id=building_id,
            footprint_area_m2=gfa_m2 / floors,
            floors=floors,
            gfa_m2=gfa_m2,
        ),
        attributes=AssignedBuildingAttributes(
            building_id=building_id,
            source_id="parcel:1",
            zone_class=ZoneClass.RESIDENTIAL,
            archetype=BuildingArchetype.POINT,
            use=use,
            floors=floors,
            config_version="buildings-v1",
        ),
    )


def test_residential_building_capacity_uses_full_gfa() -> None:
    result = ResidentialCapacityCalculator().calculate(
        (_subject("building:1", use=BuildingUse.RESIDENTIAL),),
        scenario=_scenario(),
    )

    capacity = result.buildings[0]
    assert capacity.total_gfa_m2 == 1_000.0
    assert capacity.residential_gfa_m2 == 1_000.0
    assert capacity.occupied_residential_area_m2 == 900.0
    assert capacity.resident_capacity == 30.0
    assert capacity.household_capacity == 12.0


def test_mixed_building_uses_scenario_residential_share() -> None:
    result = ResidentialCapacityCalculator().calculate(
        (_subject("building:mixed", use=BuildingUse.MIXED),),
        scenario=_scenario(residential_gfa_share=0.6),
    )

    capacity = result.buildings[0]
    assert capacity.residential_gfa_m2 == 600.0
    assert capacity.occupied_residential_area_m2 == 540.0
    assert capacity.resident_capacity == 18.0
    assert capacity.household_capacity == pytest.approx(7.2)


@pytest.mark.parametrize("use", [BuildingUse.PUBLIC, BuildingUse.COMMERCIAL])
def test_non_residential_building_has_zero_resident_capacity(
    use: BuildingUse,
) -> None:
    result = ResidentialCapacityCalculator().calculate(
        (_subject("building:nonres", use=use),),
        scenario=_scenario(),
    )

    capacity = result.buildings[0]
    assert capacity.residential_gfa_m2 == 0.0
    assert capacity.occupied_residential_area_m2 == 0.0
    assert capacity.resident_capacity == 0.0
    assert capacity.household_capacity == 0.0


def test_population_target_does_not_change_physical_capacity() -> None:
    subject = _subject("building:1", use=BuildingUse.RESIDENTIAL)
    absolute = ResidentialCapacityCalculator().calculate(
        (subject,),
        scenario=_scenario(
            target=PopulationTarget(
                kind=PopulationTargetKind.TOTAL_POPULATION,
                value=100,
            )
        ),
    )
    growth = ResidentialCapacityCalculator().calculate(
        (subject,),
        scenario=_scenario(
            target=PopulationTarget(
                kind=PopulationTargetKind.GROWTH_RATE,
                value=0.5,
            )
        ),
    )

    assert absolute.buildings == growth.buildings
    assert absolute.scenario_fingerprint != growth.scenario_fingerprint


def test_capacity_output_is_sorted_and_deterministic() -> None:
    subjects = (
        _subject("building:b", use=BuildingUse.MIXED),
        _subject("building:a", use=BuildingUse.RESIDENTIAL),
    )
    calculator = ResidentialCapacityCalculator()

    first = calculator.calculate(subjects, scenario=_scenario())
    second = calculator.calculate(tuple(reversed(subjects)), scenario=_scenario())

    assert first == second
    assert [item.building_id for item in first.buildings] == [
        "building:a",
        "building:b",
    ]


def test_subject_rejects_metric_attribute_id_mismatch() -> None:
    metrics = BuildingAreaMetrics(
        building_id="building:metrics",
        footprint_area_m2=100.0,
        floors=2,
        gfa_m2=200.0,
    )
    attributes = _subject(
        "building:attributes",
        use=BuildingUse.RESIDENTIAL,
        gfa_m2=200.0,
    ).attributes

    with pytest.raises(ResidentialCapacityError, match="ids must match"):
        ResidentialCapacitySubject(metrics=metrics, attributes=attributes)


def test_subject_rejects_metric_attribute_floor_mismatch() -> None:
    metrics = BuildingAreaMetrics(
        building_id="building:1",
        footprint_area_m2=100.0,
        floors=2,
        gfa_m2=200.0,
    )
    attributes = _subject(
        "building:1",
        use=BuildingUse.RESIDENTIAL,
        gfa_m2=300.0,
        floors=3,
    ).attributes

    with pytest.raises(ResidentialCapacityError, match="floors must match"):
        ResidentialCapacitySubject(metrics=metrics, attributes=attributes)


def test_calculator_rejects_duplicate_building_ids() -> None:
    subject = _subject("building:1", use=BuildingUse.RESIDENTIAL)

    with pytest.raises(ResidentialCapacityError, match="ids must be unique"):
        ResidentialCapacityCalculator().calculate(
            (subject, subject),
            scenario=_scenario(),
        )


def test_calculator_bounds_subject_count() -> None:
    subjects = (
        _subject("building:1", use=BuildingUse.RESIDENTIAL),
        _subject("building:2", use=BuildingUse.RESIDENTIAL),
    )

    with pytest.raises(ResidentialCapacityError, match="limit exceeded"):
        ResidentialCapacityCalculator(max_subjects=1).calculate(
            subjects,
            scenario=_scenario(),
        )
