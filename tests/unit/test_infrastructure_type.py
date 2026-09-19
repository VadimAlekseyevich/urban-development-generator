from __future__ import annotations

import math

import pytest

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.infrastructure import (
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureType,
    InfrastructureTypeError,
)
from core.urban_generator.zoning import ZoneClass


def _school() -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code="school.general",
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=0.8,
        ),
        capacity=600.0,
        max_network_distance_m=1_500.0,
        allowed_zones=(
            ZoneClass.PUBLIC,
            ZoneClass.RESIDENTIAL,
            ZoneClass.MIXED,
        ),
        minimum_site_area_m2=6_000.0,
        target_site_area_m2=12_000.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(
                InfrastructureCandidateSource.PARCEL,
                InfrastructureCandidateSource.BLOCK,
            )
        ),
    )


def test_minimum_v1_categories_are_explicit() -> None:
    assert {item.value for item in InfrastructureCategory} == {
        "education",
        "healthcare",
        "retail",
        "recreation",
    }


def test_infrastructure_type_exposes_required_constraints() -> None:
    school = _school()

    assert school.category is InfrastructureCategory.EDUCATION
    assert school.demand_model.signal is DemographicDemandCategory.AGE_GROUP
    assert school.demand_model.demographic_group == "child"
    assert school.demand_model.demand_rate == pytest.approx(0.8)
    assert school.capacity == pytest.approx(600.0)
    assert school.max_network_distance_m == pytest.approx(1_500.0)
    assert school.minimum_site_area_m2 == pytest.approx(6_000.0)
    assert school.target_site_area_m2 == pytest.approx(12_000.0)
    assert set(school.allowed_zones) == {
        ZoneClass.MIXED,
        ZoneClass.PUBLIC,
        ZoneClass.RESIDENTIAL,
    }


def test_fingerprint_is_canonical_for_zone_and_candidate_order() -> None:
    first = _school()
    second = InfrastructureType(
        version=first.version,
        code=first.code,
        category=first.category,
        demand_model=first.demand_model,
        capacity=first.capacity,
        max_network_distance_m=first.max_network_distance_m,
        allowed_zones=tuple(reversed(first.allowed_zones)),
        minimum_site_area_m2=first.minimum_site_area_m2,
        target_site_area_m2=first.target_site_area_m2,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=tuple(reversed(first.candidate_policy.sources))
        ),
    )

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert all(ch in "0123456789abcdef" for ch in first.fingerprint)


def test_age_group_demand_requires_demographic_group() -> None:
    with pytest.raises(InfrastructureTypeError, match="demographic_group"):
        InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demand_rate=1.0,
        )


@pytest.mark.parametrize(
    "signal",
    [
        DemographicDemandCategory.POPULATION,
        DemographicDemandCategory.WORKFORCE,
        DemographicDemandCategory.JOBS,
    ],
)
def test_non_age_demand_rejects_demographic_group(
    signal: DemographicDemandCategory,
) -> None:
    with pytest.raises(InfrastructureTypeError, match="only valid"):
        InfrastructureDemandModel(
            signal=signal,
            demographic_group="child",
            demand_rate=1.0,
        )


@pytest.mark.parametrize("rate", [0.0, -1.0, math.inf, math.nan])
def test_demand_rate_must_be_finite_positive(rate: float) -> None:
    with pytest.raises(InfrastructureTypeError):
        InfrastructureDemandModel(
            signal=DemographicDemandCategory.POPULATION,
            demand_rate=rate,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("capacity", 0.0),
        ("capacity", math.inf),
        ("max_network_distance_m", 0.0),
        ("minimum_site_area_m2", 0.0),
        ("target_site_area_m2", math.nan),
    ],
)
def test_numeric_constraints_are_finite_positive(
    field: str,
    value: float,
) -> None:
    kwargs = {
        "version": "infrastructure-v1",
        "code": "clinic.primary",
        "category": InfrastructureCategory.HEALTHCARE,
        "demand_model": InfrastructureDemandModel(
            signal=DemographicDemandCategory.POPULATION,
            demand_rate=0.1,
        ),
        "capacity": 500.0,
        "max_network_distance_m": 2_000.0,
        "allowed_zones": (ZoneClass.PUBLIC, ZoneClass.MIXED),
        "minimum_site_area_m2": 2_000.0,
        "target_site_area_m2": 4_000.0,
        "candidate_policy": InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    }
    kwargs[field] = value

    with pytest.raises(InfrastructureTypeError):
        InfrastructureType(**kwargs)


def test_target_site_area_cannot_be_below_minimum() -> None:
    school = _school()

    with pytest.raises(InfrastructureTypeError, match="target_site_area"):
        InfrastructureType(
            version=school.version,
            code=school.code,
            category=school.category,
            demand_model=school.demand_model,
            capacity=school.capacity,
            max_network_distance_m=school.max_network_distance_m,
            allowed_zones=school.allowed_zones,
            minimum_site_area_m2=10_000.0,
            target_site_area_m2=9_000.0,
            candidate_policy=school.candidate_policy,
        )


def test_allowed_zones_and_candidate_sources_must_not_be_empty() -> None:
    school = _school()

    with pytest.raises(InfrastructureTypeError, match="allowed_zones"):
        InfrastructureType(
            version=school.version,
            code=school.code,
            category=school.category,
            demand_model=school.demand_model,
            capacity=school.capacity,
            max_network_distance_m=school.max_network_distance_m,
            allowed_zones=(),
            minimum_site_area_m2=school.minimum_site_area_m2,
            target_site_area_m2=school.target_site_area_m2,
            candidate_policy=school.candidate_policy,
        )

    with pytest.raises(InfrastructureTypeError, match="must not be empty"):
        InfrastructureCandidatePolicy(sources=())


def test_demand_model_reuses_s09_signal_contract() -> None:
    retail = InfrastructureType(
        version="infrastructure-v1",
        code="retail.local",
        category=InfrastructureCategory.RETAIL,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.POPULATION,
            demand_rate=0.05,
        ),
        capacity=250.0,
        max_network_distance_m=800.0,
        allowed_zones=(ZoneClass.MIXED, ZoneClass.RESIDENTIAL),
        minimum_site_area_m2=200.0,
        target_site_area_m2=500.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(
                InfrastructureCandidateSource.BUILDING,
                InfrastructureCandidateSource.PARCEL,
            )
        ),
    )

    assert retail.demand_model.signal.value == "population"
    assert retail.demand_model.demographic_group is None
