from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaSubject,
    BuildingUse,
)
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
    evaluate_infrastructure_feasibility,
)
from core.urban_generator.zoning import ZoneClass

TYPE_CODE = "school.general"
WORKING_SRID = 3857


def _type(
    *,
    capacity: float = 600.0,
    minimum_site_area_m2: float = 6_000.0,
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=1.0,
        ),
        capacity=capacity,
        max_network_distance_m=1_500.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=minimum_site_area_m2,
        target_site_area_m2=max(minimum_site_area_m2, 12_000.0),
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(
                InfrastructureCandidateSource.PARCEL,
                InfrastructureCandidateSource.BUILDING,
            )
        ),
    )


def _site(*, width: float = 100.0, height: float = 100.0) -> InfrastructureCandidateGeometry:
    geometry = box(0.0, 0.0, width, height)
    return InfrastructureCandidateGeometry(
        candidate_id="candidate-site",
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id="parcel-a",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(width / 2.0, height / 2.0),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=geometry,
        site_area_m2=float(geometry.area),
        zone_id="zone-a",
        block_id="block-a",
    )


def _host_candidate() -> InfrastructureCandidateGeometry:
    return InfrastructureCandidateGeometry(
        candidate_id="candidate-host",
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.BUILDING,
        source_id="building-a",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(50.0, 50.0),
        kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
        host_building_id="building-a",
    )


def _host_building(
    *,
    building_id: str = "building-a",
    width: float = 100.0,
    height: float = 100.0,
    working_srid: int = WORKING_SRID,
) -> BuildingAreaSubject:
    attributes = AssignedBuildingAttributes(
        building_id=building_id,
        source_id=f"source-{building_id}",
        zone_class=ZoneClass.PUBLIC,
        archetype=BuildingArchetype.PUBLIC,
        use=BuildingUse.PUBLIC,
        floors=2,
        config_version="building-v1",
    )
    return BuildingAreaSubject(
        building_id=building_id,
        geometry=box(0.0, 0.0, width, height),
        attributes=attributes,
        working_srid=working_srid,
    )


def test_site_feasibility_uses_existing_t05_area_without_regeneration() -> None:
    candidate = _site()
    original_wkb = candidate.site_geometry.wkb if candidate.site_geometry is not None else None

    result = evaluate_infrastructure_feasibility(
        candidate,
        infrastructure_type=_type(),
        proposed_capacity=500.0,
    )

    assert result.is_feasible is True
    assert result.rejection_reasons == ()
    assert candidate.site_geometry is not None
    assert candidate.site_geometry.wkb == original_wkb


def test_site_feasibility_reports_capacity_and_minimum_area_rejections() -> None:
    result = evaluate_infrastructure_feasibility(
        _site(width=50.0, height=100.0),
        infrastructure_type=_type(capacity=600.0),
        proposed_capacity=700.0,
    )

    assert result.is_feasible is False
    assert result.rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.CAPACITY_EXCEEDS_TYPE_CAPACITY,
        InfrastructureFeasibilityRejectionReason.SITE_AREA_BELOW_MINIMUM,
    )


def test_site_feasibility_accepts_exact_capacity_and_area_boundaries() -> None:
    result = evaluate_infrastructure_feasibility(
        _site(width=60.0, height=100.0),
        infrastructure_type=_type(
            capacity=600.0,
            minimum_site_area_m2=6_000.0,
        ),
        proposed_capacity=600.0,
    )

    assert result.is_feasible is True


def test_host_building_feasibility_uses_explicit_building_geometry() -> None:
    result = evaluate_infrastructure_feasibility(
        _host_candidate(),
        infrastructure_type=_type(),
        proposed_capacity=500.0,
        host_building=_host_building(),
    )

    assert result.is_feasible is True
    assert result.geometry_kind is InfrastructureCandidateGeometryKind.HOST_BUILDING


def test_host_building_feasibility_reports_missing_geometry() -> None:
    result = evaluate_infrastructure_feasibility(
        _host_candidate(),
        infrastructure_type=_type(),
        proposed_capacity=500.0,
    )

    assert result.is_feasible is False
    assert result.rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.HOST_BUILDING_GEOMETRY_UNAVAILABLE,
    )


def test_host_building_feasibility_reports_small_envelope() -> None:
    result = evaluate_infrastructure_feasibility(
        _host_candidate(),
        infrastructure_type=_type(),
        proposed_capacity=500.0,
        host_building=_host_building(width=50.0, height=100.0),
    )

    assert result.is_feasible is False
    assert result.rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.HOST_BUILDING_AREA_BELOW_MINIMUM,
    )


def test_host_building_id_mismatch_is_contract_error() -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="host_building id must match",
    ):
        evaluate_infrastructure_feasibility(
            _host_candidate(),
            infrastructure_type=_type(),
            proposed_capacity=500.0,
            host_building=_host_building(building_id="building-b"),
        )


def test_host_building_working_srid_mismatch_is_contract_error() -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="host_building working_srid must match",
    ):
        evaluate_infrastructure_feasibility(
            _host_candidate(),
            infrastructure_type=_type(),
            proposed_capacity=500.0,
            host_building=_host_building(working_srid=32637),
        )


def test_site_candidate_rejects_host_building_argument() -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="site candidate must not receive host_building",
    ):
        evaluate_infrastructure_feasibility(
            _site(),
            infrastructure_type=_type(),
            proposed_capacity=500.0,
            host_building=_host_building(),
        )


def test_candidate_infrastructure_type_mismatch_is_contract_error() -> None:
    other_type = InfrastructureType(
        version="infrastructure-v1",
        code="school.other",
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=1.0,
        ),
        capacity=600.0,
        max_network_distance_m=1_500.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=6_000.0,
        target_site_area_m2=12_000.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )

    with pytest.raises(
        InfrastructureFeasibilityError,
        match="candidate infrastructure type must match",
    ):
        evaluate_infrastructure_feasibility(
            _site(),
            infrastructure_type=other_type,
            proposed_capacity=500.0,
        )
