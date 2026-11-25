import pytest
from shapely.geometry import LineString, box

from core.urban_generator.blocks import (
    BlockPolygonizationError,
    RoadNetworkBlockPolygonizer,
)
from core.urban_generator.domain import WorldStateContract
from core.urban_generator.roads import NodedRoad, RoadGraph, RoadGraphBuilder, RoadGraphInput

WORKING_SRID = 3857


def _graph(
    lines: tuple[tuple[tuple[float, float], ...], ...],
    *,
    working_srid: int = WORKING_SRID,
) -> RoadGraph:
    inputs = tuple(
        RoadGraphInput(
            road=NodedRoad(
                road_id=f"road-{index:03d}",
                parts=(LineString(coordinates),),
            ),
            state=WorldStateContract.fixed_source(),
        )
        for index, coordinates in enumerate(lines)
    )
    return RoadGraphBuilder(working_srid=working_srid).build(inputs)


def _square_lines(
    *,
    min_x: float = 0.0,
    min_y: float = 0.0,
    max_x: float = 10.0,
    max_y: float = 10.0,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    return (
        ((min_x, min_y), (max_x, min_y)),
        ((max_x, min_y), (max_x, max_y)),
        ((max_x, max_y), (min_x, max_y)),
        ((min_x, max_y), (min_x, min_y)),
    )


def test_polygonizes_closed_road_ring_into_candidate_block() -> None:
    graph = _graph(_square_lines())

    result = RoadNetworkBlockPolygonizer(working_srid=WORKING_SRID).polygonize(graph)

    assert result.working_crs.srid == WORKING_SRID
    assert len(result.blocks) == 1
    assert result.blocks[0].block_id == "block:00000000"
    assert result.blocks[0].geometry.equals(box(0, 0, 10, 10))
    assert result.diagnostics.input_edge_count == 4
    assert result.diagnostics.unique_line_count == 4
    assert result.diagnostics.duplicate_line_count == 0
    assert result.diagnostics.candidate_block_count == 1
    assert result.diagnostics.non_polygonized_line_count == 0


def test_polygonizes_adjacent_blocks_with_shared_road_boundary() -> None:
    graph = _graph(
        (
            ((0, 0), (10, 0)),
            ((10, 0), (20, 0)),
            ((20, 0), (20, 10)),
            ((20, 10), (10, 10)),
            ((10, 10), (0, 10)),
            ((0, 10), (0, 0)),
            ((10, 0), (10, 10)),
        )
    )

    result = RoadNetworkBlockPolygonizer(working_srid=WORKING_SRID).polygonize(graph)

    assert len(result.blocks) == 2
    assert sum(block.geometry.area for block in result.blocks) == pytest.approx(200.0)
    assert result.blocks[0].geometry.intersection(result.blocks[1].geometry).area == 0.0
    assert result.diagnostics.candidate_block_count == 2


def test_geometric_crossing_without_graph_node_does_not_invent_block_junction() -> None:
    graph = _graph(
        _square_lines()
        + (
            ((5, -5), (5, 15)),
        )
    )

    result = RoadNetworkBlockPolygonizer(working_srid=WORKING_SRID).polygonize(graph)

    assert len(result.blocks) == 1
    assert result.blocks[0].geometry.equals(box(0, 0, 10, 10))
    assert result.diagnostics.non_polygonized_line_count == 1


def test_exact_reversed_duplicate_line_is_deduplicated_before_polygonize() -> None:
    graph = _graph(
        _square_lines()
        + (
            ((10, 0), (0, 0)),
        )
    )

    result = RoadNetworkBlockPolygonizer(working_srid=WORKING_SRID).polygonize(graph)

    assert len(result.blocks) == 1
    assert result.diagnostics.input_edge_count == 5
    assert result.diagnostics.unique_line_count == 4
    assert result.diagnostics.duplicate_line_count == 1


def test_candidate_identity_is_deterministic_for_reversed_line_directions() -> None:
    forward = _graph(_square_lines())
    reversed_directions = _graph(
        tuple(tuple(reversed(coordinates)) for coordinates in reversed(_square_lines()))
    )
    polygonizer = RoadNetworkBlockPolygonizer(working_srid=WORKING_SRID)

    first = polygonizer.polygonize(forward)
    second = polygonizer.polygonize(reversed_directions)

    assert first.blocks == second.blocks
    assert first.diagnostics == second.diagnostics


def test_empty_road_graph_returns_no_candidates() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(())

    result = RoadNetworkBlockPolygonizer(working_srid=WORKING_SRID).polygonize(graph)

    assert result.blocks == ()
    assert result.diagnostics.input_edge_count == 0
    assert result.diagnostics.candidate_block_count == 0


def test_polygonizer_requires_matching_metric_crs_and_bounded_input() -> None:
    graph = _graph(_square_lines())

    with pytest.raises(BlockPolygonizationError, match="graph working CRS must match"):
        RoadNetworkBlockPolygonizer(working_srid=3395).polygonize(graph)

    with pytest.raises(
        BlockPolygonizationError,
        match="polygonize edge limit exceeded: 4 > 3",
    ):
        RoadNetworkBlockPolygonizer(
            working_srid=WORKING_SRID,
            max_edges=3,
        ).polygonize(graph)


def test_polygonizer_enforces_candidate_block_limit() -> None:
    graph = _graph(
        (
            ((0, 0), (10, 0)),
            ((10, 0), (20, 0)),
            ((20, 0), (20, 10)),
            ((20, 10), (10, 10)),
            ((10, 10), (0, 10)),
            ((0, 10), (0, 0)),
            ((10, 0), (10, 10)),
        )
    )

    with pytest.raises(
        BlockPolygonizationError,
        match="candidate block limit exceeded: 2 > 1",
    ):
        RoadNetworkBlockPolygonizer(
            working_srid=WORKING_SRID,
            max_candidate_blocks=1,
        ).polygonize(graph)


def test_touching_hole_repair_preserves_area_without_road_graph_changes() -> None:
    # GEOS polygonize_full produces an invalid outer face whose hole touches
    # the exterior at two non-noded points. The two remaining faces are valid.
    graph = _graph(_square_lines() + (((0, 5), (5, 8), (10, 5), (5, 2), (0, 5)),))
    before = tuple(edge.geometry.wkb for edge in graph.edges)
    result = RoadNetworkBlockPolygonizer(working_srid=WORKING_SRID).polygonize(graph)
    assert result.diagnostics.repaired_candidate_count == 1
    assert result.diagnostics.repaired_part_count == 2
    assert len(result.blocks) == 3
    assert all(item.geometry.is_valid for item in result.blocks)
    assert sum(item.geometry.area for item in result.blocks) == pytest.approx(100)
    for i, first in enumerate(result.blocks):
        for second in result.blocks[i + 1:]:
            assert first.geometry.intersection(second.geometry).area == 0
    assert tuple(edge.geometry.wkb for edge in graph.edges) == before


def test_pedestrian_path_does_not_split_block_when_excluded() -> None:
    graph = _graph((
        ((0, 0), (5, 0)), ((5, 0), (10, 0)),
        ((10, 0), (10, 10)), ((10, 10), (5, 10)),
        ((5, 10), (0, 10)), ((0, 10), (0, 0)),
        ((5, 0), (5, 10)),
    ))
    polygonizer = RoadNetworkBlockPolygonizer(working_srid=WORKING_SRID)
    assert len(polygonizer.polygonize(graph).blocks) == 2
    result = polygonizer.polygonize(
        graph, excluded_road_ids=frozenset({"road-006"}),
    )
    assert len(result.blocks) == 1
    assert result.blocks[0].geometry.equals(box(0, 0, 10, 10))
    assert result.diagnostics.excluded_road_edge_count == 1
    assert len(graph.edges) == 7
