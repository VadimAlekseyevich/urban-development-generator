from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.infrastructure import (
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureFeasibilityError,
    InfrastructureFeasibilityRejectionReason,
    InfrastructureType,
    validate_infrastructure_candidate_feasibility,
)
from core.urban_generator.zoning import ZoneClass

TYPE_CODE = "school.general"
WORKING_SRID = 3857


def _type(
    *,
    capacity: float = 600.0,
    minimum_site_area_m2: float = 100.0,
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=0.8,
        ),
        capacity=capacity,
        max_network_distance_m=1_500.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=minimum_site_area_m2,
        target_site_area_m2=max(minimum_site_area_m2, 200.0),
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(
                InfrastructureCandidateSource.PARCEL,
                InfrastructureCandidateSource.BUILDING,
            )
        ),
    )


def _site(*, area_m2: float) -> InfrastructureCandidateGeometry:
    geometry = box(0.0, 0.0, area_m2, 1.0)
    return InfrastructureCandidateGeometry(
        candidate_id="candidate-site",
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id="parcel-1",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(0.5, 0.5),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=geometry,
        site_area_m2=float(geometry.area),
        zone_id="zone-1",
        block_id="block-1",
    )


def _host() -> InfrastructureCandidateGeometry:
    return InfrastructureCandidateGeometry(
        candidate_id="candidate-host",
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.BUILDING,
        source_id="building-1",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(5.0, 5.0),
        kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
        host_building_id="building-1",
        zone_id="zone-1",
        block_id="block-1",
    )


def test_site_feasibility_uses_prepared_t05_geometry_without_regeneration() -> None:
    candidate = _site(area_m2=150.0)
    original_geometry = candidate.site_geometry

    result = validate_infrastructure_candidate_feasibility(
        candidate,
        infrastructure_type=_type(),
        proposed_capacity=600.0,
    )

    assert result.is_feasible is True
    assert result.rejection_reasons == ()
    assert candidate.site_geometry is original_geometry


def test_site_feasibility_collects_capacity_and_minimum_area_rejections() -> None:
    result = validate_infrastructure_candidate_feasibility(
        _site(area_m2=99.0),
        infrastructure_type=_type(),
        proposed_capacity=601.0,
    )

    assert result.is_feasible is False
    assert result.rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.CAPACITY_EXCEEDS_TYPE_CAPACITY,
        InfrastructureFeasibilityRejectionReason.SITE_AREA_BELOW_MINIMUM,
    )


def test_site_feasibility_accepts_exact_capacity_and_area_boundaries() -> None:
    result = validate_infrastructure_candidate_feasibility(
        _site(area_m2=100.0),
        infrastructure_type=_type(),
        proposed_capacity=600.0,
    )

    assert result.is_feasible is True


def test_host_building_without_explicit_geometry_is_rejected_not_invented() -> None:
    result = validate_infrastructure_candidate_feasibility(
        _host(),
        infrastructure_type=_type(),
        proposed_capacity=500.0,
    )

    assert result.is_feasible is False
    assert result.rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.HOST_BUILDING_GEOMETRY_UNAVAILABLE,
    )


def test_host_building_uses_explicit_geometry_area_in_same_working_crs() -> None:
    geometry = box(0.0, 0.0, 50.0, 1.0)

    result = validate_infrastructure_candidate_feasibility(
        _host(),
        infrastructure_type=_type(),
        proposed_capacity=500.0,
        host_building_geometry=geometry,
        host_building_working_srid=WORKING_SRID,
    )

    assert result.is_feasible is False
    assert result.rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.HOST_BUILDING_AREA_BELOW_MINIMUM,
    )


def test_host_building_accepts_explicit_geometry_at_minimum_area() -> None:
    geometry = box(0.0, 0.0, 100.0, 1.0)

    result = validate_infrastructure_candidate_feasibility(
        _host(),
        infrastructure_type=_type(),
        proposed_capacity=500.0,
        host_building_geometry=geometry,
        host_building_working_srid=WORKING_SRID,
    )

    assert result.is_feasible is True
    assert result.rejection_reasons == ()


def test_host_building_geometry_requires_matching_working_srid() -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="working SRID must match",
    ):
        validate_infrastructure_candidate_feasibility(
            _host(),
            infrastructure_type=_type(),
            proposed_capacity=500.0,
            host_building_geometry=box(0.0, 0.0, 100.0, 1.0),
            host_building_working_srid=32637,
        )


def test_validator_rejects_candidate_type_mismatch_as_contract_error() -> None:
    other_type = InfrastructureType(
        version="infrastructure-v1",
        code="clinic.primary",
        category=InfrastructureCategory.HEALTHCARE,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.POPULATION,
            demand_rate=0.1,
        ),
        capacity=600.0,
        max_network_distance_m=1_500.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=100.0,
        target_site_area_m2=200.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )

    with pytest.raises(
        InfrastructureFeasibilityError,
        match="candidate infrastructure type must match",
    ):
        validate_infrastructure_candidate_feasibility(
            _site(area_m2=150.0),
            infrastructure_type=other_type,
            proposed_capacity=500.0,
        )


def test_site_candidate_rejects_host_building_geometry_inputs() -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="must not receive host-building geometry",
    ):
        validate_infrastructure_candidate_feasibility(
            _site(area_m2=150.0),
            infrastructure_type=_type(),
            proposed_capacity=500.0,
            host_building_geometry=box(0.0, 0.0, 100.0, 1.0),
            host_building_working_srid=WORKING_SRID,
        )
