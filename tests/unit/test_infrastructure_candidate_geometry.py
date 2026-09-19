from __future__ import annotations

import pytest
from shapely.geometry import box

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.infrastructure import (
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSiteDiagnostics,
    InfrastructureCandidateSiteResult,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureSiteCandidate,
    InfrastructureType,
)
from core.urban_generator.infrastructure.site_geometry import (
    InfrastructureCandidateGeometryBuilder,
    InfrastructureCandidateGeometryError,
    InfrastructureCandidateGeometryKind,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _type(
    *,
    code: str = "school.general",
    target_site_area_m2: float = 2500.0,
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=code,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demand_rate=0.5,
            demographic_group="child",
        ),
        capacity=500.0,
        max_network_distance_m=1500.0,
        allowed_zones=(ZoneClass.PUBLIC, ZoneClass.MIXED),
        minimum_site_area_m2=500.0,
        target_site_area_m2=target_site_area_m2,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(
                InfrastructureCandidateSource.BLOCK,
                InfrastructureCandidateSource.PARCEL,
                InfrastructureCandidateSource.BUILDING,
            )
        ),
    )


def _candidate(
    source_kind: InfrastructureCandidateSource,
    source_id: str,
    *,
    geometry,
    zone_id: str | None = "zone-public",
    block_id: str | None = None,
) -> InfrastructureSiteCandidate:
    if source_kind is InfrastructureCandidateSource.BLOCK:
        block_id = source_id
    if source_kind is InfrastructureCandidateSource.BUILDING:
        zone_id = None
        block_id = None
    return InfrastructureSiteCandidate(
        candidate_id=f"school.general:{source_kind.value}:{source_id}",
        infrastructure_type_code="school.general",
        source_kind=source_kind,
        source_id=source_id,
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        source_geometry=geometry,
        available_area_m2=float(geometry.area),
        anchor=geometry.representative_point(),
        zone_id=zone_id,
        block_id=block_id,
    )


def _result(
    candidates: tuple[InfrastructureSiteCandidate, ...],
) -> InfrastructureCandidateSiteResult:
    return InfrastructureCandidateSiteResult(
        infrastructure_type_code="school.general",
        working_srid=WORKING_SRID,
        candidates=tuple(sorted(candidates, key=lambda item: item.candidate_id)),
        diagnostics=InfrastructureCandidateSiteDiagnostics(
            block_input_count=sum(
                item.source_kind is InfrastructureCandidateSource.BLOCK
                for item in candidates
            ),
            parcel_input_count=sum(
                item.source_kind is InfrastructureCandidateSource.PARCEL
                for item in candidates
            ),
            building_input_count=sum(
                item.source_kind is InfrastructureCandidateSource.BUILDING
                for item in candidates
            ),
            candidate_count=len(candidates),
            skipped_unzoned_count=0,
            skipped_disallowed_zone_count=0,
        ),
    )


def test_block_candidate_materializes_target_area_polygon() -> None:
    source = box(0, 0, 100, 100)
    candidate = _candidate(
        InfrastructureCandidateSource.BLOCK,
        "block-1",
        geometry=source,
    )

    result = InfrastructureCandidateGeometryBuilder().build(
        _result((candidate,)),
        infrastructure_type=_type(target_site_area_m2=2500.0),
    )

    geometry = result.candidates[0]
    assert geometry.kind is InfrastructureCandidateGeometryKind.SITE
    assert geometry.host_building_id is None
    assert geometry.site_geometry is not None
    assert geometry.site_area_m2 == pytest.approx(2500.0, rel=1e-6)
    assert source.covers(geometry.site_geometry)
    assert geometry.site_geometry.covers(geometry.anchor)
    assert geometry.site_geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert result.diagnostics.clipped_site_count == 1
    assert result.diagnostics.full_source_site_count == 0


def test_parcel_candidate_uses_full_source_when_target_is_larger() -> None:
    source = box(0, 0, 20, 20)
    candidate = _candidate(
        InfrastructureCandidateSource.PARCEL,
        "parcel-1",
        geometry=source,
        block_id="block-1",
    )

    result = InfrastructureCandidateGeometryBuilder().build(
        _result((candidate,)),
        infrastructure_type=_type(target_site_area_m2=2500.0),
    )

    geometry = result.candidates[0]
    assert geometry.kind is InfrastructureCandidateGeometryKind.SITE
    assert geometry.site_geometry is source
    assert geometry.site_area_m2 == pytest.approx(400.0)
    assert geometry.block_id == "block-1"
    assert geometry.zone_id == "zone-public"
    assert result.diagnostics.clipped_site_count == 0
    assert result.diagnostics.full_source_site_count == 1


def test_building_candidate_becomes_explicit_host_reference() -> None:
    source = box(0, 0, 30, 20)
    candidate = _candidate(
        InfrastructureCandidateSource.BUILDING,
        "building-1",
        geometry=source,
    )

    result = InfrastructureCandidateGeometryBuilder().build(
        _result((candidate,)),
        infrastructure_type=_type(),
    )

    geometry = result.candidates[0]
    assert geometry.kind is InfrastructureCandidateGeometryKind.HOST_BUILDING
    assert geometry.host_building_id == "building-1"
    assert geometry.site_geometry is None
    assert geometry.site_area_m2 is None
    assert result.diagnostics.host_building_count == 1
    assert result.diagnostics.polygon_site_count == 0


def test_mixed_result_has_no_point_only_candidate() -> None:
    block = _candidate(
        InfrastructureCandidateSource.BLOCK,
        "block-1",
        geometry=box(0, 0, 100, 100),
    )
    parcel = _candidate(
        InfrastructureCandidateSource.PARCEL,
        "parcel-1",
        geometry=box(120, 0, 170, 50),
        block_id="block-2",
    )
    building = _candidate(
        InfrastructureCandidateSource.BUILDING,
        "building-1",
        geometry=box(200, 0, 220, 20),
    )

    result = InfrastructureCandidateGeometryBuilder().build(
        _result((block, parcel, building)),
        infrastructure_type=_type(),
    )

    for item in result.candidates:
        if item.kind is InfrastructureCandidateGeometryKind.SITE:
            assert item.site_geometry is not None
            assert item.site_area_m2 is not None
        else:
            assert item.host_building_id is not None
    assert result.diagnostics.candidate_count == 3
    assert result.diagnostics.polygon_site_count == 2
    assert result.diagnostics.host_building_count == 1


def test_geometry_builder_is_deterministic() -> None:
    candidate = _candidate(
        InfrastructureCandidateSource.BLOCK,
        "block-1",
        geometry=box(0, 0, 100, 100),
    )
    builder = InfrastructureCandidateGeometryBuilder()
    source = _result((candidate,))

    first = builder.build(source, infrastructure_type=_type())
    second = builder.build(source, infrastructure_type=_type())

    assert first == second


def test_candidate_type_must_match_infrastructure_type() -> None:
    candidate = _candidate(
        InfrastructureCandidateSource.BLOCK,
        "block-1",
        geometry=box(0, 0, 100, 100),
    )

    with pytest.raises(
        InfrastructureCandidateGeometryError,
        match="must match InfrastructureType",
    ):
        InfrastructureCandidateGeometryBuilder().build(
            _result((candidate,)),
            infrastructure_type=_type(code="clinic.primary"),
        )


def test_search_iterations_must_be_positive() -> None:
    with pytest.raises(InfrastructureCandidateGeometryError):
        InfrastructureCandidateGeometryBuilder(search_iterations=0)


def test_site_metadata_preserves_source_and_zone_context() -> None:
    candidate = _candidate(
        InfrastructureCandidateSource.PARCEL,
        "parcel-42",
        geometry=box(0, 0, 100, 80),
        zone_id="zone-7",
        block_id="block-9",
    )

    result = InfrastructureCandidateGeometryBuilder().build(
        _result((candidate,)),
        infrastructure_type=_type(),
    )

    geometry = result.candidates[0]
    assert geometry.candidate_id == candidate.candidate_id
    assert geometry.source_kind is InfrastructureCandidateSource.PARCEL
    assert geometry.source_id == "parcel-42"
    assert geometry.zone_class is ZoneClass.PUBLIC
    assert geometry.zone_id == "zone-7"
    assert geometry.block_id == "block-9"
    assert geometry.working_srid == WORKING_SRID
