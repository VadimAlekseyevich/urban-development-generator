import itertools
import math

import pytest
from shapely.geometry import LineString, box
from shapely.ops import unary_union

from core.urban_generator.blocks import (
    BlockDevelopableArea,
    BlockDevelopableClippingDiagnostics,
    BlockDevelopableClippingResult,
    BlockFrontageValidator,
    BlockHardConstraintLayer,
    BlockMetricsCalculator,
    BlockPolygonizationDiagnostics,
    BlockPolygonizationResult,
    BlockZoneAssociator,
    BlockZoneReference,
    CandidateBlock,
    CleanedBlockCandidate,
    DevelopableBlockCandidate,
    DevelopableBlockClipper,
    OversizedBlockSplitPolicy,
    OversizedBlockSplitter,
    ParcelSubdivisionPolicy,
    SimplifiedParcelSubdivider,
    SplitBlockCandidate,
)
from core.urban_generator.domain import WorkingCRS, WorldStateContract
from core.urban_generator.roads import NodedRoad, RoadGraph, RoadGraphBuilder, RoadGraphInput
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857
AREA_TOLERANCE_M2 = 1e-6


def _road_graph(
    roads: tuple[tuple[str, tuple[tuple[float, float], ...]], ...],
) -> RoadGraph:
    return RoadGraphBuilder(working_srid=WORKING_SRID).build(
        tuple(
            RoadGraphInput(
                road=NodedRoad(
                    road_id=road_id,
                    parts=(LineString(coordinates),),
                ),
                state=WorldStateContract.fixed_source(),
            )
            for road_id, coordinates in roads
        )
    )


def _polygonization(*geometries) -> BlockPolygonizationResult:
    blocks = tuple(
        CandidateBlock(block_id=f"candidate:{index:03d}", geometry=geometry)
        for index, geometry in enumerate(geometries)
    )
    return BlockPolygonizationResult(
        working_crs=WorkingCRS(srid=WORKING_SRID),
        blocks=blocks,
        diagnostics=BlockPolygonizationDiagnostics(
            input_edge_count=0,
            unique_line_count=0,
            duplicate_line_count=0,
            candidate_block_count=len(blocks),
            cut_edge_count=0,
            dangle_edge_count=0,
            invalid_ring_count=0,
        ),
    )


def _cleaned_block(block_id: str, geometry) -> CleanedBlockCandidate:
    member = SplitBlockCandidate(
        block_id=f"split:{block_id}",
        input_block_id=f"input:{block_id}",
        source_block_id=f"source:{block_id}",
        source_fragment_index=0,
        split_path=(),
        geometry=geometry,
    )
    return CleanedBlockCandidate(
        block_id=block_id,
        members=(member,),
        geometry=geometry,
    )


def _zoned_grid(
    geometries: tuple[tuple[str, object], ...],
):
    from core.urban_generator.blocks import (
        SliverCleanupDiagnostics,
        SliverCleanupPolicy,
        SliverCleanupResult,
    )

    cleaned = tuple(
        _cleaned_block(block_id, geometry)
        for block_id, geometry in geometries
    )
    total_area = math.fsum(float(block.geometry.area) for block in cleaned)
    cleanup = SliverCleanupResult(
        working_crs=WorkingCRS(srid=WORKING_SRID),
        policy=SliverCleanupPolicy(min_area_m2=1.0),
        blocks=cleaned,
        events=(),
        diagnostics=SliverCleanupDiagnostics(
            input_block_count=len(cleaned),
            input_sliver_count=0,
            output_block_count=len(cleaned),
            merged_event_count=0,
            dropped_event_count=0,
            kept_unresolved_event_count=0,
            operation_count=0,
            spatial_candidate_pair_count=0,
            adjacency_pair_count=0,
            input_area_m2=total_area,
            output_area_m2=total_area,
            dropped_area_m2=0.0,
        ),
    )
    zones = tuple(
        BlockZoneReference(
            zone_id=f"zone:{block_id}",
            zone_class=ZoneClass.RESIDENTIAL,
            geometry=geometry,
            working_srid=WORKING_SRID,
        )
        for block_id, geometry in geometries
    )
    return BlockZoneAssociator(working_srid=WORKING_SRID).associate(
        cleanup,
        zones=zones,
    )


def test_clipping_outputs_stay_inside_boundary_exclusions_and_do_not_overlap() -> None:
    project_boundary = box(0, 0, 100, 80)
    developable_mask = box(5, 5, 95, 75)
    exclusion = box(45, 0, 55, 80)
    source_blocks = (
        box(-10, -10, 40, 40),
        box(40, -10, 70, 40),
        box(70, -10, 110, 40),
        box(-10, 40, 40, 90),
        box(40, 40, 70, 90),
        box(70, 40, 110, 90),
    )

    result = DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
        _polygonization(*source_blocks),
        area=BlockDevelopableArea(
            project_boundary=project_boundary,
            developable_mask=developable_mask,
            working_srid=WORKING_SRID,
        ),
        hard_constraints=(
            BlockHardConstraintLayer(
                code="test.exclusion",
                geometries=(exclusion,),
                working_srid=WORKING_SRID,
            ),
        ),
    )

    effective = project_boundary.intersection(developable_mask)
    assert result.blocks
    assert all(block.geometry.is_valid and not block.geometry.is_empty for block in result.blocks)
    assert all(effective.covers(block.geometry) for block in result.blocks)
    assert all(
        block.geometry.intersection(exclusion).area <= AREA_TOLERANCE_M2
        for block in result.blocks
    )
    for first, second in itertools.combinations(result.blocks, 2):
        assert first.geometry.intersection(second.geometry).area <= AREA_TOLERANCE_M2


def test_parcel_grid_preserves_block_coverage_non_overlap_and_road_access() -> None:
    dimensions = (
        (28.0, 20.0),
        (31.0, 24.0),
        (36.0, 22.0),
        (42.0, 20.0),
        (47.0, 26.0),
        (53.0, 24.0),
        (58.0, 28.0),
        (64.0, 30.0),
    )
    geometries: list[tuple[str, object]] = []
    roads: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    for index, (width, height) in enumerate(dimensions):
        x = index * 90.0
        geometry = box(x, 0.0, x + width, height)
        block_id = f"block:{index:03d}"
        geometries.append((block_id, geometry))
        roads.append(
            (
                f"road:{index:03d}",
                ((x, 0.0), (x + width, 0.0)),
            )
        )

    zoned = _zoned_grid(tuple(geometries))
    graph = _road_graph(tuple(roads))
    result = SimplifiedParcelSubdivider(
        working_srid=WORKING_SRID,
        policy=ParcelSubdivisionPolicy(
            target_frontage_m=10.0,
            minimum_frontage_m=7.0,
            minimum_parcel_area_m2=100.0,
        ),
    ).subdivide(zoned, road_graph=graph)

    parent_by_id = dict(geometries)
    road_ids = {edge.road_id for edge in graph.edges}

    assert result.diagnostics.parceled_block_count == len(geometries)
    assert result.diagnostics.skipped_block_count == 0
    assert result.diagnostics.road_candidate_pair_count == len(geometries)
    assert result.diagnostics.frontage_overlap_pair_count == len(geometries)

    for block_id, parent in geometries:
        parcels = tuple(parcel for parcel in result.parcels if parcel.block_id == block_id)
        assert parcels
        assert unary_union(tuple(parcel.geometry for parcel in parcels)).area == pytest.approx(
            parent.area,
            abs=AREA_TOLERANCE_M2,
        )
        for parcel in parcels:
            assert parcel.geometry.is_valid and not parcel.geometry.is_empty
            assert parent_by_id[parcel.block_id].covers(parcel.geometry)
            assert parcel.buildable_envelope.within(parent_by_id[parcel.block_id])
            assert parcel.has_frontage
            assert parcel.frontage_length_m >= 7.0 - 1e-6
            assert set(parcel.frontage_road_ids) <= road_ids
            assert parcel.zone_id == f"zone:{block_id}"
            assert parcel.zone_class is ZoneClass.RESIDENTIAL
            for frontage in parcel.frontages:
                assert frontage.geometry.difference(parcel.geometry.boundary).length <= 1e-6
        for first, second in itertools.combinations(parcels, 2):
            assert first.geometry.intersection(second.geometry).area <= AREA_TOLERANCE_M2


@pytest.mark.parametrize("width_m", (10.0, 20.0, 35.0, 60.0, 95.0, 160.0))
def test_oversized_split_respects_area_and_depth_bounds_across_sizes(width_m: float) -> None:
    source = box(0.0, 0.0, width_m, 10.0)
    clipped = BlockDevelopableClippingResult(
        working_crs=WorkingCRS(srid=WORKING_SRID),
        blocks=(
            # T05 consumes T02 geometry through metrics/frontage; this fixture keeps the
            # source fragment identity stable while varying only its metric size.
            DevelopableBlockCandidate(
                block_id="block:input",
                source_block_id="source:input",
                source_fragment_index=0,
                geometry=source,
            ),
        ),
        diagnostics=BlockDevelopableClippingDiagnostics(
            input_block_count=1,
            output_block_count=1,
            mask_clipped_block_count=0,
            mask_removed_block_count=0,
            hard_constraint_hit_block_count=0,
            hard_constraint_removed_block_count=0,
            split_source_block_count=0,
            hard_constraint_geometry_count=0,
            constraint_candidate_count=0,
            validity_repair_count=0,
        ),
    )
    metrics = BlockMetricsCalculator(working_srid=WORKING_SRID).calculate(clipped)
    graph = _road_graph(())
    frontage = BlockFrontageValidator(working_srid=WORKING_SRID).validate(
        metrics,
        road_graph=graph,
    )
    result = OversizedBlockSplitter(
        working_srid=WORKING_SRID,
        policy=OversizedBlockSplitPolicy(max_area_m2=100.0),
        max_split_depth=8,
        max_split_operations=64,
        max_output_blocks=64,
    ).split(frontage, road_graph=graph)

    assert math.fsum(block.geometry.area for block in result.blocks) == pytest.approx(source.area)
    assert all(block.geometry.is_valid for block in result.blocks)
    assert all(block.geometry.area <= 100.0 + AREA_TOLERANCE_M2 for block in result.blocks)
    assert result.diagnostics.max_observed_split_depth <= 8
    assert result.diagnostics.split_attempt_count <= 64
    assert len(result.blocks) <= 64
