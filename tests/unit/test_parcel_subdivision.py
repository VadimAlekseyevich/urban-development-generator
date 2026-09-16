import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from core.urban_generator.blocks import (
    BlockSliverCleaner,
    BlockZoneAssociator,
    BlockZoneReference,
    OversizedBlockSplitDiagnostics,
    OversizedBlockSplitPolicy,
    OversizedBlockSplitResult,
    ParcelSubdivisionError,
    ParcelSubdivisionPolicy,
    ParcelSubdivisionSkipReason,
    SimplifiedParcelSubdivider,
    SliverCleanupPolicy,
    SplitBlockCandidate,
)
from core.urban_generator.domain import WorkingCRS, WorldStateContract
from core.urban_generator.roads import NodedRoad, RoadGraph, RoadGraphBuilder, RoadGraphInput
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _split_block(block_id: str, geometry) -> SplitBlockCandidate:
    return SplitBlockCandidate(
        block_id=block_id,
        input_block_id=f"input:{block_id}",
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


def _zoned(
    geometry,
    *,
    zone_class: ZoneClass = ZoneClass.RESIDENTIAL,
    zone_geometry=None,
    working_srid: int = WORKING_SRID,
):
    cleanup = BlockSliverCleaner(
        working_srid=working_srid,
        policy=SliverCleanupPolicy(min_area_m2=1.0),
    ).cleanup(
        _split_result(
            _split_block("split:a", geometry),
            working_srid=working_srid,
        )
    )
    zone = BlockZoneReference(
        zone_id="zone:a",
        zone_class=zone_class,
        geometry=zone_geometry if zone_geometry is not None else geometry,
        working_srid=working_srid,
    )
    return BlockZoneAssociator(working_srid=working_srid).associate(
        cleanup,
        zones=(zone,),
    )


def _road_graph(
    *roads: tuple[str, tuple[tuple[float, float], ...]],
    working_srid: int = WORKING_SRID,
) -> RoadGraph:
    return RoadGraphBuilder(working_srid=working_srid).build(
        tuple(
            RoadGraphInput(
                road=NodedRoad(road_id=road_id, parts=(LineString(coordinates),)),
                state=WorldStateContract.fixed_source(),
            )
            for road_id, coordinates in roads
        )
    )


def _policy(**overrides) -> ParcelSubdivisionPolicy:
    values = {
        "target_frontage_m": 10.0,
        "minimum_frontage_m": 8.0,
        "minimum_parcel_area_m2": 100.0,
    }
    values.update(overrides)
    return ParcelSubdivisionPolicy(**values)


def _subdivide(zoned, graph: RoadGraph, **kwargs):
    return SimplifiedParcelSubdivider(
        working_srid=WORKING_SRID,
        policy=_policy(),
        **kwargs,
    ).subdivide(zoned, road_graph=graph)


def test_rectangular_residential_block_subdivides_perpendicular_to_frontage() -> None:
    block = box(0, 0, 40, 20)
    result = _subdivide(
        _zoned(block),
        _road_graph(("front", ((0, 0), (40, 0)))),
    )

    assert len(result.parcels) == 4
    assert tuple(parcel.parcel_id for parcel in result.parcels) == (
        "parcel:00000000",
        "parcel:00000001",
        "parcel:00000002",
        "parcel:00000003",
    )
    assert all(parcel.zone_id == "zone:a" for parcel in result.parcels)
    assert all(parcel.zone_class is ZoneClass.RESIDENTIAL for parcel in result.parcels)
    assert all(parcel.area_m2 == pytest.approx(200.0) for parcel in result.parcels)
    assert all(parcel.frontage_length_m == pytest.approx(10.0) for parcel in result.parcels)
    assert all(parcel.frontage_road_ids == ("front",) for parcel in result.parcels)
    assert all(parcel.buildable_envelope.equals(parcel.geometry) for parcel in result.parcels)
    assert unary_union(tuple(parcel.geometry for parcel in result.parcels)).equals(block)

    decision = result.decisions[0]
    assert decision.skip_reason is None
    assert decision.selected_frontage_road_id == "front"
    assert decision.selected_frontage_length_m == pytest.approx(40.0)
    assert len(decision.parcel_ids) == 4
    assert result.diagnostics.parceled_block_count == 1
    assert result.diagnostics.subdivided_block_count == 1
    assert result.diagnostics.parcel_count == 4
    assert result.diagnostics.parceled_area_m2 == pytest.approx(800.0)


def test_minimum_area_caps_parcel_count() -> None:
    block = box(0, 0, 40, 20)
    policy = _policy(minimum_parcel_area_m2=250.0)
    result = SimplifiedParcelSubdivider(
        working_srid=WORKING_SRID,
        policy=policy,
    ).subdivide(
        _zoned(block),
        road_graph=_road_graph(("front", ((0, 0), (40, 0)))),
    )

    assert len(result.parcels) == 3
    assert all(parcel.area_m2 >= 250.0 for parcel in result.parcels)
    assert sum(parcel.area_m2 for parcel in result.parcels) == pytest.approx(800.0)


def test_small_suitable_residential_block_becomes_one_planning_parcel() -> None:
    block = box(0, 0, 9, 20)
    result = _subdivide(
        _zoned(block),
        _road_graph(("front", ((0, 0), (9, 0)))),
    )

    assert len(result.parcels) == 1
    assert result.parcels[0].geometry.equals(block)
    assert result.parcels[0].frontage_length_m == pytest.approx(9.0)
    assert result.diagnostics.single_parcel_block_count == 1
    assert result.diagnostics.subdivided_block_count == 0


def test_non_residential_block_produces_no_parcels() -> None:
    block = box(0, 0, 40, 20)
    result = _subdivide(
        _zoned(block, zone_class=ZoneClass.MIXED),
        _road_graph(("front", ((0, 0), (40, 0)))),
    )

    assert result.parcels == ()
    assert result.decisions[0].skip_reason is ParcelSubdivisionSkipReason.NON_RESIDENTIAL
    assert result.diagnostics.residential_associated_block_count == 0


def test_unassociated_residential_overlap_produces_no_parcels() -> None:
    block = box(0, 0, 40, 20)
    partial_zone = box(0, 0, 20, 20)
    result = _subdivide(
        _zoned(block, zone_geometry=partial_zone),
        _road_graph(("front", ((0, 0), (40, 0)))),
    )

    assert result.parcels == ()
    assert result.decisions[0].skip_reason is ParcelSubdivisionSkipReason.UNASSOCIATED_ZONE


def test_transverse_road_crossing_is_not_frontage() -> None:
    block = box(0, 0, 40, 20)
    result = _subdivide(
        _zoned(block),
        _road_graph(("cross", ((20, -10), (20, 30)))),
    )

    assert result.parcels == ()
    assert result.decisions[0].skip_reason is ParcelSubdivisionSkipReason.NO_FRONTAGE


def test_insufficient_frontage_is_explicit() -> None:
    block = box(0, 0, 40, 20)
    result = _subdivide(
        _zoned(block),
        _road_graph(("short", ((0, 0), (5, 0)))),
    )

    decision = result.decisions[0]
    assert decision.skip_reason is ParcelSubdivisionSkipReason.INSUFFICIENT_FRONTAGE
    assert decision.selected_frontage_road_id == "short"
    assert decision.selected_frontage_length_m == pytest.approx(5.0)


def test_holed_residential_block_is_conservatively_skipped() -> None:
    block = Polygon(
        shell=[(0, 0), (40, 0), (40, 20), (0, 20), (0, 0)],
        holes=[[(10, 5), (20, 5), (20, 15), (10, 15), (10, 5)]],
    )
    result = _subdivide(
        _zoned(block),
        _road_graph(("front", ((0, 0), (40, 0)))),
    )

    assert result.parcels == ()
    assert result.decisions[0].skip_reason is ParcelSubdivisionSkipReason.HOLED_BLOCK


def test_bent_frontage_below_linearity_threshold_is_skipped() -> None:
    block = Polygon([(0, 0), (5, -2), (10, 0), (10, 10), (0, 10), (0, 0)])
    graph = _road_graph(("bent", ((0, 0), (5, -2), (10, 0))))
    policy = _policy(
        target_frontage_m=5.0,
        minimum_frontage_m=4.0,
        minimum_parcel_area_m2=20.0,
    )
    result = SimplifiedParcelSubdivider(
        working_srid=WORKING_SRID,
        policy=policy,
    ).subdivide(_zoned(block), road_graph=graph)

    assert result.parcels == ()
    assert result.decisions[0].skip_reason is ParcelSubdivisionSkipReason.NONLINEAR_FRONTAGE


def test_duplicate_overlapping_road_geometry_does_not_double_frontage() -> None:
    block = box(0, 0, 20, 20)
    result = _subdivide(
        _zoned(block),
        _road_graph(
            ("alpha", ((0, 0), (20, 0))),
            ("beta", ((20, 0), (0, 0))),
        ),
    )

    assert len(result.parcels) == 2
    assert all(parcel.frontage_length_m == pytest.approx(10.0) for parcel in result.parcels)
    assert all(parcel.frontage_road_ids == ("alpha",) for parcel in result.parcels)


def test_output_is_deterministic_for_road_input_order() -> None:
    block = box(0, 0, 40, 20)
    first = _subdivide(
        _zoned(block),
        _road_graph(
            ("bottom", ((0, 0), (40, 0))),
            ("left", ((0, 0), (0, 20))),
        ),
    )
    second = _subdivide(
        _zoned(block),
        _road_graph(
            ("left", ((0, 0), (0, 20))),
            ("bottom", ((0, 0), (40, 0))),
        ),
    )

    assert tuple(parcel.geometry.wkb_hex for parcel in first.parcels) == tuple(
        parcel.geometry.wkb_hex for parcel in second.parcels
    )
    assert tuple(parcel.frontage_road_ids for parcel in first.parcels) == tuple(
        parcel.frontage_road_ids for parcel in second.parcels
    )
    assert first.decisions == second.decisions


def test_block_too_small_for_minimum_area_is_skipped() -> None:
    block = box(0, 0, 8, 10)
    result = _subdivide(
        _zoned(block),
        _road_graph(("front", ((0, 0), (8, 0)))),
    )

    assert result.parcels == ()
    assert result.decisions[0].skip_reason is ParcelSubdivisionSkipReason.BLOCK_TOO_SMALL


def test_bounds_and_crs_guards_are_enforced() -> None:
    block = box(0, 0, 40, 20)
    zoned = _zoned(block)
    graph = _road_graph(("front", ((0, 0), (40, 0))))

    with pytest.raises(ParcelSubdivisionError, match="block limit exceeded"):
        SimplifiedParcelSubdivider(
            working_srid=WORKING_SRID,
            policy=_policy(),
            max_blocks=1,
        ).subdivide(
            BlockZoneAssociator(working_srid=WORKING_SRID).associate(
                BlockSliverCleaner(
                    working_srid=WORKING_SRID,
                    policy=SliverCleanupPolicy(min_area_m2=1.0),
                ).cleanup(
                    _split_result(
                        _split_block("split:a", block),
                        _split_block("split:b", box(50, 0, 90, 20)),
                    )
                ),
                zones=(
                    BlockZoneReference(
                        zone_id="zone:all",
                        zone_class=ZoneClass.RESIDENTIAL,
                        geometry=box(-10, -10, 100, 30),
                        working_srid=WORKING_SRID,
                    ),
                ),
            ),
            road_graph=graph,
        )

    with pytest.raises(ParcelSubdivisionError, match="road edge limit exceeded"):
        SimplifiedParcelSubdivider(
            working_srid=WORKING_SRID,
            policy=_policy(),
            max_road_edges=1,
        ).subdivide(
            zoned,
            road_graph=_road_graph(
                ("front-a", ((0, 0), (20, 0))),
                ("front-b", ((20, 0), (40, 0))),
            ),
        )

    with pytest.raises(ParcelSubdivisionError, match="road candidate limit exceeded"):
        SimplifiedParcelSubdivider(
            working_srid=WORKING_SRID,
            policy=_policy(),
            max_road_candidates_per_block=1,
        ).subdivide(
            zoned,
            road_graph=_road_graph(
                ("front-a", ((0, 0), (20, 0))),
                ("front-b", ((20, 0), (40, 0))),
            ),
        )

    with pytest.raises(ParcelSubdivisionError, match="road graph working CRS"):
        SimplifiedParcelSubdivider(
            working_srid=WORKING_SRID,
            policy=_policy(),
        ).subdivide(
            zoned,
            road_graph=_road_graph(
                ("front", ((0, 0), (40, 0))),
                working_srid=3395,
            ),
        )


def test_output_parcel_limit_is_hard_bound() -> None:
    block = box(0, 0, 40, 20)
    with pytest.raises(ParcelSubdivisionError, match="output parcel limit exceeded"):
        SimplifiedParcelSubdivider(
            working_srid=WORKING_SRID,
            policy=_policy(),
            max_output_parcels=3,
        ).subdivide(
            _zoned(block),
            road_graph=_road_graph(("front", ((0, 0), (40, 0)))),
        )


def test_policy_validation_rejects_invalid_values() -> None:
    with pytest.raises(ParcelSubdivisionError, match="target_frontage_m"):
        ParcelSubdivisionPolicy(
            target_frontage_m=0.0,
            minimum_frontage_m=8.0,
            minimum_parcel_area_m2=100.0,
        )
    with pytest.raises(ParcelSubdivisionError, match="minimum_frontage_linearity_ratio"):
        ParcelSubdivisionPolicy(
            target_frontage_m=10.0,
            minimum_frontage_m=8.0,
            minimum_parcel_area_m2=100.0,
            minimum_frontage_linearity_ratio=1.1,
        )
