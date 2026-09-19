from __future__ import annotations

import math

import pytest

from core.urban_generator.demography import (
    AgeGroupShare,
    DemographicScenario,
    DemographicScenarioError,
    PopulationTarget,
    PopulationTargetKind,
)


def _age_groups() -> tuple[AgeGroupShare, ...]:
    return (
        AgeGroupShare(code="0-6", min_age=0, max_age=6, share=0.08),
        AgeGroupShare(code="7-17", min_age=7, max_age=17, share=0.12),
        AgeGroupShare(code="18-64", min_age=18, max_age=64, share=0.65),
        AgeGroupShare(code="65+", min_age=65, max_age=None, share=0.15),
    )


def _scenario(
    *,
    age_groups: tuple[AgeGroupShare, ...] | None = None,
) -> DemographicScenario:
    return DemographicScenario(
        version="demography-v1",
        population_target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=25_000,
        ),
        occupancy_ratio=0.92,
        residential_area_per_person_m2=32.0,
        average_household_size=2.4,
        residential_gfa_share=0.85,
        age_groups=_age_groups() if age_groups is None else age_groups,
        working_population_ratio=0.58,
    )


def test_scenario_exposes_canonical_demographic_assumptions() -> None:
    scenario = _scenario()

    assert scenario.population_target.total_population == 25_000
    assert scenario.population_target.growth_rate is None
    assert scenario.vacancy_ratio == pytest.approx(0.08)
    assert scenario.age_group("18-64").share == 0.65
    assert [group.code for group in scenario.age_groups] == [
        "0-6",
        "7-17",
        "18-64",
        "65+",
    ]
    assert len(scenario.fingerprint) == 64


def test_growth_target_is_explicit_and_fractional() -> None:
    target = PopulationTarget(
        kind=PopulationTargetKind.GROWTH_RATE,
        value=0.125,
    )

    assert target.total_population is None
    assert target.growth_rate == 0.125


def test_age_group_input_order_does_not_change_fingerprint() -> None:
    first = _scenario(age_groups=_age_groups())
    second = _scenario(age_groups=tuple(reversed(_age_groups())))

    assert first.age_groups == second.age_groups
    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (PopulationTargetKind.TOTAL_POPULATION, -1),
        (PopulationTargetKind.TOTAL_POPULATION, 1.5),
        (PopulationTargetKind.GROWTH_RATE, -1.0),
        (PopulationTargetKind.GROWTH_RATE, -2.0),
        (PopulationTargetKind.GROWTH_RATE, math.inf),
    ],
)
def test_population_target_rejects_invalid_values(
    kind: PopulationTargetKind,
    value: int | float,
) -> None:
    with pytest.raises(DemographicScenarioError):
        PopulationTarget(kind=kind, value=value)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("occupancy_ratio", -0.01),
        ("occupancy_ratio", 1.01),
        ("residential_gfa_share", -0.01),
        ("residential_gfa_share", 1.01),
        ("working_population_ratio", -0.01),
        ("working_population_ratio", 1.01),
    ],
)
def test_scenario_rejects_out_of_range_ratios(
    field_name: str,
    value: float,
) -> None:
    kwargs = {
        "version": "demography-v1",
        "population_target": PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=1_000,
        ),
        "occupancy_ratio": 0.9,
        "residential_area_per_person_m2": 30.0,
        "average_household_size": 2.5,
        "residential_gfa_share": 0.8,
        "age_groups": _age_groups(),
        "working_population_ratio": 0.6,
    }
    kwargs[field_name] = value

    with pytest.raises(DemographicScenarioError):
        DemographicScenario(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("residential_area_per_person_m2", 0.0),
        ("residential_area_per_person_m2", -1.0),
        ("average_household_size", 0.0),
        ("average_household_size", -1.0),
    ],
)
def test_scenario_rejects_non_positive_scale_values(
    field_name: str,
    value: float,
) -> None:
    kwargs = {
        "version": "demography-v1",
        "population_target": PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=1_000,
        ),
        "occupancy_ratio": 0.9,
        "residential_area_per_person_m2": 30.0,
        "average_household_size": 2.5,
        "residential_gfa_share": 0.8,
        "age_groups": _age_groups(),
        "working_population_ratio": 0.6,
    }
    kwargs[field_name] = value

    with pytest.raises(DemographicScenarioError):
        DemographicScenario(**kwargs)  # type: ignore[arg-type]


def test_age_group_shares_must_sum_to_one() -> None:
    groups = (
        AgeGroupShare(code="0-17", min_age=0, max_age=17, share=0.2),
        AgeGroupShare(code="18+", min_age=18, max_age=None, share=0.7),
    )

    with pytest.raises(DemographicScenarioError, match="shares must sum"):
        _scenario(age_groups=groups)


@pytest.mark.parametrize(
    "groups",
    [
        (
            AgeGroupShare(code="1-17", min_age=1, max_age=17, share=0.2),
            AgeGroupShare(code="18+", min_age=18, max_age=None, share=0.8),
        ),
        (
            AgeGroupShare(code="0-6", min_age=0, max_age=6, share=0.1),
            AgeGroupShare(code="8+", min_age=8, max_age=None, share=0.9),
        ),
        (
            AgeGroupShare(code="0-17", min_age=0, max_age=17, share=0.2),
            AgeGroupShare(code="18-64", min_age=18, max_age=64, share=0.6),
        ),
    ],
)
def test_age_groups_must_form_complete_contiguous_partition(
    groups: tuple[AgeGroupShare, ...],
) -> None:
    with pytest.raises(DemographicScenarioError):
        _scenario(age_groups=groups)


def test_age_group_codes_must_be_unique() -> None:
    groups = (
        AgeGroupShare(code="all", min_age=0, max_age=17, share=0.2),
        AgeGroupShare(code="all", min_age=18, max_age=None, share=0.8),
    )

    with pytest.raises(DemographicScenarioError, match="codes must be unique"):
        _scenario(age_groups=groups)
