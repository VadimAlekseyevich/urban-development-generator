from __future__ import annotations

import pytest
from shapely.geometry import box

from core.urban_generator.blocks import (
    BlockZoneAssociation,
    BlockZoneAssociationDiagnostics,
    BlockZoneAssociationResult,
    BlockZoneAssociationStatus,
    CleanedBlockCandidate,
    PlanningParcel,
    SplitBlockCandidate,
    ZoneAssociatedBlock,
)
from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaSubject,
    BuildingUse,
)
from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import WorkingCRS
from core.urban_generator.infrastructure import (
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSiteError,
    InfrastructureCandidateSiteGenerator,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureType,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _type(
    *,
    sources: tuple[InfrastructureCandidateSource, ...] = (
        InfrastructureCandidateSource.BLOCK,
        InfrastructureCandidateSource.PARCEL,
        InfrastructureCandidateSource.BUILDING,
    ),
    allowed_zones: tuple[ZoneClass, ...] = (
        ZoneClass.PUBLIC,
        ZoneClass.MIXED,
    ),
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code="clinic.primary",
        category=InfrastructureCategory.HEALTHCARE,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.POPULATION,
            demand_rate=0.01,
        ),
        capacity=250.0,
        max_network_distance_m=2000.0,
        allowed_zones=allowed_zones,
        minimum_site_area_m2=500.0,
        target_site_area_m2=1000.0,
        candidate_policy=InfrastructureCandidatePolicy(sources=sources),
    )


def _cleaned_block(block_id: str, *, x0: float) -> CleanedBlockCandidate:
    member = SplitBlockCandidate(
        block_id="split:" + block_id,
        input_block_id="input:" + block_id,
        source_block_id="source:" + block_id,
        source_fragment_index=0,
        split_path=(),
        geometry=box(x0, 0, x0 + 100, 100),
    )
    return CleanedBlockCandidate(
        block_id=block_id,
        members=(member,),
        geometry=box(x0, 0, x0 + 100, 100),
    )


def _zoned_blocks() -> BlockZoneAssociationResult:
    public = ZoneAssociatedBlock(
        cleaned_block=_cleaned_block("block-public", x0=0),
        association=BlockZoneAssociation(
            status=BlockZoneAssociationStatus.ASSOCIATED,
            zone_id="zone-public",
            zone_class=ZoneClass.PUBLIC,
            positive_overlap_zone_ids=("zone-public",),
            maximum_overlap_ratio=1.0,
        ),
    )
    residential = ZoneAssociatedBlock(
        cleaned_block=_cleaned_block("block-residential", x0=120),
        association=BlockZoneAssociation(
            status=BlockZoneAssociationStatus.ASSOCIATED,
            zone_id="zone-residential",
            zone_class=ZoneClass.RESIDENTIAL,
            positive_overlap_zone_ids=("zone-residential",),
            maximum_overlap_ratio=1.0,
        ),
    )
    unzoned = ZoneAssociatedBlock(
        cleaned_block=_cleaned_block("block-unzoned", x0=240),
        association=BlockZoneAssociation(
            status=BlockZoneAssociationStatus.NO_OVERLAP,
            zone_id=None,
            zone_class=None,
            positive_overlap_zone_ids=(),
            maximum_overlap_ratio=0.0,
        ),
    )
    return BlockZoneAssociationResult(
        working_crs=WorkingCRS(srid=WORKING_SRID),
        blocks=(public, residential, unzoned),
        diagnostics=BlockZoneAssociationDiagnostics(
            block_count=3,
            zone_count=2,
            associated_block_count=2,
            no_overlap_block_count=1,
            partial_overlap_block_count=0,
            ambiguous_block_count=0,
            spatial_candidate_pair_count=2,
            positive_overlap_pair_count=2,
        ),
    )


def _parcels() -> tuple[PlanningParcel, ...]:
    return (
        PlanningParcel(
            parcel_id="parcel-public",
            block_id="block-public",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 50, 100),
            buildable_envelope=box(5, 5, 45, 95),
            zone_id="zone-public",
            zone_class=ZoneClass.PUBLIC,
        ),
        PlanningParcel(
            parcel_id="parcel-residential",
            block_id="block-residential",
            working_srid=WORKING_SRID,
            geometry=box(120, 0, 170, 100),
            buildable_envelope=box(125, 5, 165, 95),
            zone_id="zone-residential",
            zone_class=ZoneClass.RESIDENTIAL,
        ),
        PlanningParcel(
            parcel_id="parcel-unzoned",
            block_id="block-unzoned",
            working_srid=WORKING_SRID,
            geometry=box(240, 0, 290, 100),
            buildable_envelope=box(245, 5, 285, 95),
        ),
    )


def _building(
    building_id: str,
    *,
    zone_class: ZoneClass,
    x0: float,
    working_srid: int = WORKING_SRID,
) -> BuildingAreaSubject:
    attributes = AssignedBuildingAttributes(
        building_id=building_id,
        source_id="source:" + building_id,
        zone_class=zone_class,
        archetype=BuildingArchetype.PUBLIC,
        use=BuildingUse.PUBLIC,
        floors=2,
        config_version="buildings-v1",
    )
    return BuildingAreaSubject(
        building_id=building_id,
        geometry=box(x0, 0, x0 + 20, 20),
        attributes=attributes,
        working_srid=working_srid,
    )


def test_generator_emits_all_enabled_allowed_source_kinds() -> None:
    result = InfrastructureCandidateSiteGenerator(
        working_srid=WORKING_SRID
    ).generate(
        _type(),
        zoned_blocks=_zoned_blocks(),
        parcels=_parcels(),
        buildings=(
            _building("building-public", zone_class=ZoneClass.PUBLIC, x0=400),
            _building("building-mixed", zone_class=ZoneClass.MIXED, x0=430),
            _building(
                "building-residential",
                zone_class=ZoneClass.RESIDENTIAL,
                x0=460,
            ),
        ),
    )

    assert [item.source_kind for item in result.candidates] == [
        InfrastructureCandidateSource.BLOCK,
        InfrastructureCandidateSource.BUILDING,
        InfrastructureCandidateSource.BUILDING,
        InfrastructureCandidateSource.PARCEL,
    ]
    assert [item.source_id for item in result.candidates] == [
        "block-public",
        "building-mixed",
        "building-public",
        "parcel-public",
    ]
    assert result.diagnostics.block_input_count == 3
    assert result.diagnostics.parcel_input_count == 3
    assert result.diagnostics.building_input_count == 3
    assert result.diagnostics.skipped_unzoned_count == 2
    assert result.diagnostics.skipped_disallowed_zone_count == 3


def test_parcel_candidate_uses_buildable_envelope_as_available_site() -> None:
    parcel = _parcels()[0]
    result = InfrastructureCandidateSiteGenerator(
        working_srid=WORKING_SRID
    ).generate(
        _type(sources=(InfrastructureCandidateSource.PARCEL,)),
        parcels=(parcel,),
    )

    candidate = result.candidates[0]
    assert candidate.source_id == parcel.parcel_id
    assert candidate.block_id == parcel.block_id
    assert candidate.zone_id == parcel.zone_id
    assert candidate.source_geometry.equals(parcel.buildable_envelope)
    assert candidate.available_area_m2 == pytest.approx(
        parcel.buildable_envelope.area
    )
    assert candidate.source_geometry.covers(candidate.anchor)


def test_building_candidate_is_explicit_host_without_invented_block() -> None:
    building = _building(
        "building-public",
        zone_class=ZoneClass.PUBLIC,
        x0=0,
    )
    result = InfrastructureCandidateSiteGenerator(
        working_srid=WORKING_SRID
    ).generate(
        _type(sources=(InfrastructureCandidateSource.BUILDING,)),
        buildings=(building,),
    )

    candidate = result.candidates[0]
    assert candidate.source_kind is InfrastructureCandidateSource.BUILDING
    assert candidate.source_id == building.building_id
    assert candidate.block_id is None
    assert candidate.zone_id is None
    assert candidate.source_geometry.equals(building.geometry)


def test_candidate_policy_excludes_disabled_source_kinds() -> None:
    result = InfrastructureCandidateSiteGenerator(
        working_srid=WORKING_SRID
    ).generate(
        _type(sources=(InfrastructureCandidateSource.BUILDING,)),
        zoned_blocks=_zoned_blocks(),
        parcels=_parcels(),
        buildings=(
            _building("building-public", zone_class=ZoneClass.PUBLIC, x0=400),
        ),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].source_kind is InfrastructureCandidateSource.BUILDING
    assert result.diagnostics.block_input_count == 3
    assert result.diagnostics.parcel_input_count == 3


def test_minimum_site_area_is_not_prematurely_filtered_in_t04() -> None:
    tiny = _building(
        "building-tiny",
        zone_class=ZoneClass.PUBLIC,
        x0=0,
    )
    assert tiny.geometry.area < _type().minimum_site_area_m2

    result = InfrastructureCandidateSiteGenerator(
        working_srid=WORKING_SRID
    ).generate(
        _type(sources=(InfrastructureCandidateSource.BUILDING,)),
        buildings=(tiny,),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].available_area_m2 == pytest.approx(
        tiny.geometry.area
    )


def test_candidate_ids_are_deterministic_for_input_order() -> None:
    generator = InfrastructureCandidateSiteGenerator(
        working_srid=WORKING_SRID
    )
    infrastructure_type = _type(
        sources=(InfrastructureCandidateSource.BUILDING,)
    )
    first = _building(
        "building-b",
        zone_class=ZoneClass.PUBLIC,
        x0=0,
    )
    second = _building(
        "building-a",
        zone_class=ZoneClass.PUBLIC,
        x0=30,
    )

    left = generator.generate(
        infrastructure_type,
        buildings=(first, second),
    )
    right = generator.generate(
        infrastructure_type,
        buildings=(second, first),
    )

    assert left == right
    assert [item.candidate_id for item in left.candidates] == [
        "clinic.primary:building:building-a",
        "clinic.primary:building:building-b",
    ]


def test_candidate_limit_is_enforced_after_filtering() -> None:
    with pytest.raises(
        InfrastructureCandidateSiteError,
        match="candidate limit exceeded",
    ):
        InfrastructureCandidateSiteGenerator(
            working_srid=WORKING_SRID,
            max_candidates=1,
        ).generate(
            _type(sources=(InfrastructureCandidateSource.BUILDING,)),
            buildings=(
                _building("building-a", zone_class=ZoneClass.PUBLIC, x0=0),
                _building("building-b", zone_class=ZoneClass.PUBLIC, x0=30),
            ),
        )


def test_input_limit_bounds_all_source_collections() -> None:
    with pytest.raises(
        InfrastructureCandidateSiteError,
        match="input limit exceeded",
    ):
        InfrastructureCandidateSiteGenerator(
            working_srid=WORKING_SRID,
            max_inputs=1,
        ).generate(
            _type(sources=(InfrastructureCandidateSource.BUILDING,)),
            parcels=_parcels()[:1],
            buildings=(
                _building("building-a", zone_class=ZoneClass.PUBLIC, x0=0),
            ),
        )


def test_building_working_srid_must_match_generator() -> None:
    with pytest.raises(
        InfrastructureCandidateSiteError,
        match="building working_srid",
    ):
        InfrastructureCandidateSiteGenerator(
            working_srid=WORKING_SRID
        ).generate(
            _type(sources=(InfrastructureCandidateSource.BUILDING,)),
            buildings=(
                _building(
                    "building-a",
                    zone_class=ZoneClass.PUBLIC,
                    x0=0,
                    working_srid=32637,
                ),
            ),
        )
