import pytest
from shapely.geometry import LineString, box

from core.urban_generator.blocks import (
    BlockSliverCleaner,
    BlockZoneAssociationError,
    BlockZoneAssociationStatus,
    BlockZoneAssociator,
    BlockZoneReference,
    OversizedBlockSplitDiagnostics,
    OversizedBlockSplitPolicy,
    OversizedBlockSplitResult,
    SliverCleanupPolicy,
    SplitBlockCandidate,
)
from core.urban_generator.domain import WorkingCRS
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _split_block(
    block_id: str,
    geometry,
    *,
    input_block_id: str | None = None,
) -> SplitBlockCandidate:
    return SplitBlockCandidate(
        block_id=block_id,
        input_block_id=input_block_id or f"input:{block_id}",
        source_block_id=f"source:{block_id}",
        source_fragment_index=0,
        split_path=(),
        geometry=geometry,
    )


def _split_result(
    *blocks: SplitBlockCandidate,
    working_srid: int = WORKING_SRID,
) -> OversizedBlockSplitResult:
    return OversizedBlockSplitResult(
        working_crs=WorkingCRS(srid=working_srid),
        policy=OversizedBlockSplitPolicy(max_area_m2=1_000_000.0),
        blocks=tuple(blocks),
        diagnostics=OversizedBlockSplitDiagnostics(
            input_block_count=len(blocks),
            oversized_input_block_count=0,
            output_block_count=len(blocks),
            split_attempt_count=0,
            successful_split_count=0,
            road_informed_split_count=0,
            principal_axis_split_count=0,
            unsplittable_fragment_count=0,
            road_candidate_pair_count=0,
            max_observed_split_depth=0,
        ),
    )


def _cleanup(*blocks: SplitBlockCandidate, working_srid: int = WORKING_SRID):
    return BlockSliverCleaner(
        working_srid=working_srid,
        policy=SliverCleanupPolicy(min_area_m2=1.0),
    ).cleanup(_split_result(*blocks, working_srid=working_srid))


def _zone(
    zone_id: str,
    geometry,
    *,
    zone_class: ZoneClass = ZoneClass.RESIDENTIAL,
    working_srid: int = WORKING_SRID,
) -> BlockZoneReference:
    return BlockZoneReference(
        zone_id=zone_id,
        zone_class=zone_class,
        geometry=geometry,
        working_srid=working_srid,
    )


def test_single_full_cover_zone_is_associated() -> None:
    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    zone = _zone(
        "zone:residential",
        box(-1, -1, 11, 11),
        zone_class=ZoneClass.RESIDENTIAL,
    )

    result = BlockZoneAssociator(working_srid=WORKING_SRID).associate(
        cleanup,
        zones=(zone,),
    )

    association = result.blocks[0].association
    assert association.status is BlockZoneAssociationStatus.ASSOCIATED
    assert association.zone_id == "zone:residential"
    assert association.zone_class is ZoneClass.RESIDENTIAL
    assert association.positive_overlap_zone_ids == ("zone:residential",)
    assert association.maximum_overlap_ratio == pytest.approx(1.0)
    assert result.diagnostics.associated_block_count == 1
    assert result.diagnostics.positive_overlap_pair_count == 1


def test_boundary_touch_without_positive_area_is_no_overlap() -> None:
    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    touching_zone = _zone("zone:touch", box(10, 0, 20, 10))

    result = BlockZoneAssociator(working_srid=WORKING_SRID).associate(
        cleanup,
        zones=(touching_zone,),
    )

    association = result.blocks[0].association
    assert association.status is BlockZoneAssociationStatus.NO_OVERLAP
    assert association.zone_id is None
    assert association.positive_overlap_zone_ids == ()
    assert association.maximum_overlap_ratio == 0.0
    assert result.diagnostics.spatial_candidate_pair_count == 1
    assert result.diagnostics.positive_overlap_pair_count == 0


def test_partial_overlap_is_explicit_and_not_assigned() -> None:
    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    half_zone = _zone("zone:half", box(0, 0, 5, 10))

    result = BlockZoneAssociator(working_srid=WORKING_SRID).associate(
        cleanup,
        zones=(half_zone,),
    )

    association = result.blocks[0].association
    assert association.status is BlockZoneAssociationStatus.PARTIAL_OVERLAP
    assert association.zone_id is None
    assert association.zone_class is None
    assert association.positive_overlap_zone_ids == ("zone:half",)
    assert association.maximum_overlap_ratio == pytest.approx(0.5)


def test_block_crossing_two_zone_boundaries_is_not_dominant_zone_assigned() -> None:
    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    left = _zone("zone:left", box(0, 0, 5, 10), zone_class=ZoneClass.RESIDENTIAL)
    right = _zone("zone:right", box(5, 0, 10, 10), zone_class=ZoneClass.MIXED)

    result = BlockZoneAssociator(working_srid=WORKING_SRID).associate(
        cleanup,
        zones=(right, left),
    )

    association = result.blocks[0].association
    assert association.status is BlockZoneAssociationStatus.PARTIAL_OVERLAP
    assert association.zone_id is None
    assert association.positive_overlap_zone_ids == ("zone:left", "zone:right")
    assert association.maximum_overlap_ratio == pytest.approx(0.5)


def test_full_cover_plus_secondary_overlap_is_ambiguous() -> None:
    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    full = _zone("zone:full", box(-1, -1, 11, 11))
    secondary = _zone(
        "zone:secondary",
        box(0, 0, 2, 10),
        zone_class=ZoneClass.PUBLIC,
    )

    result = BlockZoneAssociator(working_srid=WORKING_SRID).associate(
        cleanup,
        zones=(secondary, full),
    )

    association = result.blocks[0].association
    assert association.status is BlockZoneAssociationStatus.AMBIGUOUS_FULL_COVERAGE
    assert association.zone_id is None
    assert association.positive_overlap_zone_ids == ("zone:full", "zone:secondary")
    assert association.maximum_overlap_ratio == pytest.approx(1.0)
    assert result.diagnostics.ambiguous_block_count == 1


def test_sub_tolerance_overlap_is_treated_as_no_positive_overlap() -> None:
    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    tiny_overlap = _zone("zone:tiny", box(9.99999995, 0, 20, 10))

    result = BlockZoneAssociator(working_srid=WORKING_SRID).associate(
        cleanup,
        zones=(tiny_overlap,),
    )

    assert result.blocks[0].association.status is BlockZoneAssociationStatus.NO_OVERLAP
    assert result.diagnostics.positive_overlap_pair_count == 0


def test_output_order_and_overlap_ids_are_deterministic() -> None:
    cleanup = _cleanup(
        _split_block("split:z", box(0, 0, 10, 10)),
        _split_block("split:a", box(20, 0, 30, 10)),
    )
    zones = (
        _zone("zone:z", box(0, 0, 5, 10)),
        _zone("zone:a", box(5, 0, 10, 10)),
        _zone("zone:far", box(20, 0, 30, 10), zone_class=ZoneClass.RECREATION),
    )

    result = BlockZoneAssociator(working_srid=WORKING_SRID).associate(
        cleanup,
        zones=tuple(reversed(zones)),
    )

    assert tuple(item.cleaned_block.block_id for item in result.blocks) == (
        "block:00000000",
        "block:00000001",
    )
    first = result.blocks[0].association
    second = result.blocks[1].association
    assert first.status is BlockZoneAssociationStatus.PARTIAL_OVERLAP
    assert first.positive_overlap_zone_ids == ("zone:a", "zone:z")
    assert second.status is BlockZoneAssociationStatus.ASSOCIATED
    assert second.zone_id == "zone:far"


def test_duplicate_zone_ids_are_rejected() -> None:
    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    zones = (
        _zone("zone:duplicate", box(0, 0, 5, 10)),
        _zone("zone:duplicate", box(5, 0, 10, 10)),
    )

    with pytest.raises(BlockZoneAssociationError, match="duplicate zone_id"):
        BlockZoneAssociator(working_srid=WORKING_SRID).associate(cleanup, zones=zones)


def test_zone_reference_requires_polygonal_geometry_and_matching_metric_crs() -> None:
    with pytest.raises(BlockZoneAssociationError, match="Polygon or MultiPolygon"):
        _zone("zone:line", LineString([(0, 0), (1, 1)]))

    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    wrong_crs = _zone("zone:wrong-crs", box(0, 0, 10, 10), working_srid=3395)
    with pytest.raises(BlockZoneAssociationError, match="working_srid"):
        BlockZoneAssociator(working_srid=WORKING_SRID).associate(
            cleanup,
            zones=(wrong_crs,),
        )


def test_cleanup_crs_must_match_associator_crs() -> None:
    cleanup = _cleanup(
        _split_block("split:a", box(0, 0, 10, 10)),
        working_srid=3395,
    )

    with pytest.raises(BlockZoneAssociationError, match="cleanup working CRS"):
        BlockZoneAssociator(working_srid=WORKING_SRID).associate(cleanup, zones=())


def test_block_zone_and_candidate_limits_are_bounded() -> None:
    cleanup = _cleanup(
        _split_block("split:a", box(0, 0, 10, 10)),
        _split_block("split:b", box(20, 0, 30, 10)),
    )
    with pytest.raises(BlockZoneAssociationError, match="block limit exceeded"):
        BlockZoneAssociator(working_srid=WORKING_SRID, max_blocks=1).associate(
            cleanup,
            zones=(),
        )

    single_cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))
    zones = (
        _zone("zone:a", box(0, 0, 5, 10)),
        _zone("zone:b", box(5, 0, 10, 10)),
    )
    with pytest.raises(BlockZoneAssociationError, match="zone limit exceeded"):
        BlockZoneAssociator(working_srid=WORKING_SRID, max_zones=1).associate(
            single_cleanup,
            zones=zones,
        )
    with pytest.raises(BlockZoneAssociationError, match="candidate limit exceeded"):
        BlockZoneAssociator(
            working_srid=WORKING_SRID,
            max_candidates_per_block=1,
        ).associate(single_cleanup, zones=zones)


def test_zones_input_must_be_immutable_tuple() -> None:
    cleanup = _cleanup(_split_block("split:a", box(0, 0, 10, 10)))

    with pytest.raises(BlockZoneAssociationError, match="immutable tuple"):
        BlockZoneAssociator(working_srid=WORKING_SRID).associate(  # type: ignore[arg-type]
            cleanup,
            zones=[],
        )
