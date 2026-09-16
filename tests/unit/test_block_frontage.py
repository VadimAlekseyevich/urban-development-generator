import pytest
from shapely.geometry import LineString, box

from core.urban_generator.blocks import (
    BlockDevelopableClippingDiagnostics,
    BlockDevelopableClippingResult,
    BlockFrontagePolicy,
    BlockFrontageValidationError,
    BlockFrontageValidator,
    BlockMetricsCalculator,
    BlockMetricsResult,
    DevelopableBlockCandidate,
)
from core.urban_generator.domain import WorkingCRS, WorldStateContract
from core.urban_generator.domain.crs import CRSContractError
from core.urban_generator.roads import NodedRoad, RoadGraph, RoadGraphBuilder, RoadGraphInput

WORKING_SRID = 3857


def _metrics(
    *blocks: DevelopableBlockCandidate,
    working_srid: int = WORKING_SRID,
) -> BlockMetricsResult:
    clipping = BlockDevelopableClippingResult(
        working_crs=WorkingCRS(srid=working_srid),
        blocks=tuple(blocks),
        diagnostics=BlockDevelopableClippingDiagnostics(
            input_block_count=len(blocks),
            output_block_count=len(blocks),
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
    return BlockMetricsCalculator(working_srid=working_srid).calculate(clipping)


def _block(block_id: str = "block:00000000") -> DevelopableBlockCandidate:
    return DevelopableBlockCandidate(
        block_id=block_id,
        source_block_id=f"source:{block_id}",
        source_fragment_index=0,
        geometry=box(0, 0, 10, 10),
    )


def _road_graph(
    *roads: tuple[str, tuple[tuple[float, float], ...]],
    working_srid: int = WORKING_SRID,
) -> RoadGraph:
    inputs = tuple(
        RoadGraphInput(
            road=NodedRoad(road_id=road_id, parts=(LineString(coordinates),)),
            state=WorldStateContract.fixed_source(),
        )
        for road_id, coordinates in roads
    )
    return RoadGraphBuilder(working_srid=working_srid).build(inputs)


def test_positive_boundary_overlap_is_valid_frontage_and_access() -> None:
    graph = _road_graph(
        ("bottom", ((0, 0), (10, 0))),
        ("left", ((0, 0), (0, 10))),
    )

    result = BlockFrontageValidator(working_srid=WORKING_SRID).validate(
        _metrics(_block()),
        road_graph=graph,
    )

    validation = result.blocks[0].validation
    assert validation.frontage_length_m == pytest.approx(20.0)
    assert validation.frontage_road_ids == ("bottom", "left")
    assert validation.nearest_road_distance_m == pytest.approx(0.0)
    assert validation.has_access is True
    assert validation.has_frontage is True
    assert validation.meets_minimum_frontage is True
    assert validation.is_valid is True
    assert result.diagnostics.valid_block_count == 1
    assert result.diagnostics.total_frontage_m == pytest.approx(20.0)


def test_transverse_crossing_has_zero_frontage_and_is_invalid() -> None:
    graph = _road_graph(("crossing", ((5, -5), (5, 15))))

    result = BlockFrontageValidator(working_srid=WORKING_SRID).validate(
        _metrics(_block()),
        road_graph=graph,
    )

    validation = result.blocks[0].validation
    assert validation.nearest_road_distance_m == pytest.approx(0.0)
    assert validation.has_access is True
    assert validation.frontage_length_m == 0.0
    assert validation.frontage_road_ids == ()
    assert validation.has_frontage is False
    assert validation.meets_minimum_frontage is False
    assert validation.is_valid is False
    assert result.diagnostics.no_frontage_block_count == 1


def test_access_tolerance_uses_indexed_nearest_distance_but_not_fake_frontage() -> None:
    graph = _road_graph(("nearby", ((0, -1), (10, -1))))
    policy = BlockFrontagePolicy(access_tolerance_m=1.0)

    result = BlockFrontageValidator(
        working_srid=WORKING_SRID,
        policy=policy,
    ).validate(_metrics(_block()), road_graph=graph)

    validation = result.blocks[0].validation
    assert validation.nearest_road_distance_m == pytest.approx(1.0)
    assert validation.has_access is True
    assert validation.has_frontage is False
    assert validation.is_valid is False


def test_minimum_frontage_policy_rejects_short_positive_overlap() -> None:
    graph = _road_graph(("short", ((0, 0), (4, 0))))
    policy = BlockFrontagePolicy(minimum_frontage_m=5.0)

    result = BlockFrontageValidator(
        working_srid=WORKING_SRID,
        policy=policy,
    ).validate(_metrics(_block()), road_graph=graph)

    validation = result.blocks[0].validation
    assert validation.frontage_length_m == pytest.approx(4.0)
    assert validation.has_access is True
    assert validation.has_frontage is True
    assert validation.meets_minimum_frontage is False
    assert validation.is_valid is False
    assert result.diagnostics.below_minimum_frontage_block_count == 1


def test_overlapping_parallel_edges_do_not_double_count_frontage_length() -> None:
    graph = _road_graph(
        ("alpha", ((0, 0), (10, 0))),
        ("beta", ((10, 0), (0, 0))),
    )

    result = BlockFrontageValidator(working_srid=WORKING_SRID).validate(
        _metrics(_block()),
        road_graph=graph,
    )

    validation = result.blocks[0].validation
    assert validation.frontage_length_m == pytest.approx(10.0)
    assert validation.frontage_road_ids == ("alpha", "beta")
    assert validation.is_valid is True


def test_empty_road_graph_marks_block_without_access_or_frontage() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(())

    result = BlockFrontageValidator(working_srid=WORKING_SRID).validate(
        _metrics(_block()),
        road_graph=graph,
    )

    validation = result.blocks[0].validation
    assert validation.nearest_road_distance_m is None
    assert validation.nearest_road_edge_id is None
    assert validation.has_access is False
    assert validation.has_frontage is False
    assert validation.is_valid is False
    assert result.diagnostics.no_access_block_count == 1
    assert result.diagnostics.no_frontage_block_count == 1


def test_validation_is_deterministic_for_block_input_order() -> None:
    first = _block("block:a")
    second = DevelopableBlockCandidate(
        block_id="block:b",
        source_block_id="source:b",
        source_fragment_index=0,
        geometry=box(20, 0, 30, 10),
    )
    graph = _road_graph(
        ("first", ((0, 0), (10, 0))),
        ("second", ((20, 0), (30, 0))),
    )
    validator = BlockFrontageValidator(working_srid=WORKING_SRID)

    forward = validator.validate(_metrics(first, second), road_graph=graph)
    reversed_input = validator.validate(_metrics(second, first), road_graph=graph)

    assert forward == reversed_input
    assert tuple(item.measured_block.block.block_id for item in forward.blocks) == (
        "block:a",
        "block:b",
    )


def test_requires_matching_metric_crs_for_blocks_and_road_graph() -> None:
    validator = BlockFrontageValidator(working_srid=WORKING_SRID)

    with pytest.raises(BlockFrontageValidationError, match="block metrics working CRS"):
        validator.validate(
            _metrics(_block(), working_srid=3395),
            road_graph=RoadGraphBuilder(working_srid=WORKING_SRID).build(()),
        )

    with pytest.raises(BlockFrontageValidationError, match="road graph working CRS"):
        validator.validate(
            _metrics(_block()),
            road_graph=_road_graph(("road", ((0, 0), (1, 0))), working_srid=3395),
        )

    with pytest.raises(CRSContractError, match="not projected"):
        BlockFrontageValidator(working_srid=4326)


def test_enforces_block_road_and_candidate_bounds() -> None:
    two_blocks = _metrics(
        _block("block:a"),
        DevelopableBlockCandidate(
            block_id="block:b",
            source_block_id="source:b",
            source_fragment_index=0,
            geometry=box(20, 0, 30, 10),
        ),
    )
    one_road = _road_graph(("road", ((0, 0), (10, 0))))
    with pytest.raises(BlockFrontageValidationError, match="frontage block limit exceeded"):
        BlockFrontageValidator(working_srid=WORKING_SRID, max_blocks=1).validate(
            two_blocks,
            road_graph=one_road,
        )

    two_roads = _road_graph(
        ("alpha", ((0, 0), (10, 0))),
        ("beta", ((0, 10), (10, 10))),
    )
    with pytest.raises(BlockFrontageValidationError, match="frontage road edge limit exceeded"):
        BlockFrontageValidator(working_srid=WORKING_SRID, max_road_edges=1).validate(
            _metrics(_block()),
            road_graph=two_roads,
        )

    with pytest.raises(BlockFrontageValidationError, match="frontage candidate limit exceeded"):
        BlockFrontageValidator(
            working_srid=WORKING_SRID,
            max_candidates_per_block=1,
        ).validate(_metrics(_block()), road_graph=two_roads)


def test_policy_requires_finite_non_negative_values() -> None:
    with pytest.raises(BlockFrontageValidationError, match="access_tolerance_m"):
        BlockFrontagePolicy(access_tolerance_m=-1.0)

    with pytest.raises(BlockFrontageValidationError, match="minimum_frontage_m"):
        BlockFrontagePolicy(minimum_frontage_m=float("inf"))
