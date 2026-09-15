import uuid

import pytest
from shapely.geometry import LineString

from core.urban_generator.domain import WorldStateContract
from core.urban_generator.roads import (
    NodedRoad,
    RoadGraph,
    RoadGraphBuilder,
    RoadGraphCleaner,
    RoadGraphCleanupError,
    RoadGraphCleanupPolicy,
    RoadGraphInput,
)

WORKING_SRID = 3857
RUN_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _road(
    road_id: str,
    *parts: tuple[tuple[float, float], ...],
) -> NodedRoad:
    return NodedRoad(
        road_id=road_id,
        parts=tuple(LineString(part) for part in parts),
    )


def _fixed(road: NodedRoad) -> RoadGraphInput:
    return RoadGraphInput(road=road, state=WorldStateContract.fixed_source())


def _generated(road: NodedRoad) -> RoadGraphInput:
    return RoadGraphInput(
        road=road,
        state=WorldStateContract.generated_for(RUN_ID),
    )


def _build(*roads: RoadGraphInput) -> RoadGraph:
    return RoadGraphBuilder(working_srid=WORKING_SRID).build(tuple(roads))


def _dangling_chain_graph() -> RoadGraph:
    return _build(
        _fixed(
            _road(
                "fixed-main",
                ((0.0, 0.0), (5.0, 0.0)),
                ((5.0, 0.0), (10.0, 0.0)),
            )
        ),
        _generated(
            _road(
                "generated-stub",
                ((5.0, 0.0), (5.0, 0.5)),
                ((5.0, 0.5), (5.0, 1.0)),
            )
        ),
    )


def test_exact_duplicate_cleanup_prefers_fixed_source_even_when_reversed() -> None:
    graph = _build(
        _fixed(_road("fixed", ((0.0, 0.0), (10.0, 0.0)))),
        _generated(_road("generated", ((10.0, 0.0), (0.0, 0.0)))),
    )

    result = RoadGraphCleaner().cleanup(graph)

    assert len(result.graph.edges) == 1
    assert result.graph.edges[0].road_id == "fixed"
    assert result.graph.edges[0].is_fixed is True
    assert result.graph.diagnostics.source_edge_count == 1
    assert result.graph.diagnostics.fixed_edge_count == 1
    assert result.diagnostics.duplicate_edges_removed == 1
    assert result.diagnostics.orphan_nodes_removed == 0


def test_parallel_edges_with_different_geometry_are_not_duplicates() -> None:
    graph = _build(
        _generated(_road("straight", ((0.0, 0.0), (10.0, 0.0)))),
        _generated(
            _road(
                "curved",
                ((0.0, 0.0), (5.0, 1.0), (10.0, 0.0)),
            )
        ),
    )

    result = RoadGraphCleaner().cleanup(graph)

    assert tuple(edge.road_id for edge in result.graph.edges) == ("curved", "straight")
    assert result.diagnostics.duplicate_edges_removed == 0
    assert result.graph.diagnostics.edge_count == 2


def test_tiny_cleanup_removes_generated_edges_but_preserves_fixed_source() -> None:
    graph = _build(
        _fixed(_road("fixed-tiny", ((0.0, 0.0), (0.1, 0.0)))),
        _generated(_road("generated-tiny", ((10.0, 0.0), (10.1, 0.0)))),
        _generated(_road("generated-long", ((20.0, 0.0), (21.0, 0.0)))),
    )
    cleaner = RoadGraphCleaner(RoadGraphCleanupPolicy(tiny_edge_threshold_m=0.2))

    result = cleaner.cleanup(graph)

    assert tuple(edge.road_id for edge in result.graph.edges) == (
        "fixed-tiny",
        "generated-long",
    )
    assert result.diagnostics.tiny_edges_removed == 1
    assert result.diagnostics.fixed_tiny_edges_retained == 1
    assert result.diagnostics.orphan_nodes_removed == 2
    assert result.graph.diagnostics.edge_count == 2
    assert result.graph.diagnostics.fixed_edge_count == 1


def test_dangling_cleanup_prunes_generated_chain_iteratively() -> None:
    cleaner = RoadGraphCleaner(
        RoadGraphCleanupPolicy(
            dangling_edge_threshold_m=0.5,
            max_prune_passes=4,
        )
    )

    result = cleaner.cleanup(_dangling_chain_graph())

    assert tuple(edge.road_id for edge in result.graph.edges) == (
        "fixed-main",
        "fixed-main",
    )
    assert result.diagnostics.dangling_edges_removed == 2
    assert result.diagnostics.dangling_prune_passes == 2
    assert result.diagnostics.generated_dangling_edges_remaining == 0
    assert result.diagnostics.prune_limit_reached is False
    assert result.graph.diagnostics.component_count == 1


def test_dangling_cleanup_stops_at_explicit_pass_bound_and_reports_remainder() -> None:
    cleaner = RoadGraphCleaner(
        RoadGraphCleanupPolicy(
            dangling_edge_threshold_m=0.5,
            max_prune_passes=1,
        )
    )

    result = cleaner.cleanup(_dangling_chain_graph())

    assert result.diagnostics.dangling_edges_removed == 1
    assert result.diagnostics.dangling_prune_passes == 1
    assert result.diagnostics.generated_dangling_edges_remaining == 1
    assert result.diagnostics.prune_limit_reached is True
    assert tuple(edge.road_id for edge in result.graph.edges).count("generated-stub") == 1


def test_cleanup_is_deterministic_for_reordered_graph_records() -> None:
    graph = _build(
        _fixed(_road("fixed", ((0.0, 0.0), (2.0, 0.0)))),
        _generated(_road("generated", ((2.0, 0.0), (4.0, 0.0)))),
    )
    reordered = RoadGraph(
        working_crs=graph.working_crs,
        nodes=tuple(reversed(graph.nodes)),
        edges=tuple(reversed(graph.edges)),
        diagnostics=graph.diagnostics,
    )
    cleaner = RoadGraphCleaner()

    forward = cleaner.cleanup(graph)
    reverse = cleaner.cleanup(reordered)

    assert forward.graph == reverse.graph
    assert forward.diagnostics == reverse.diagnostics


def test_cleanup_policy_and_work_bounds_are_validated() -> None:
    with pytest.raises(RoadGraphCleanupError, match="tiny_edge_threshold_m"):
        RoadGraphCleanupPolicy(tiny_edge_threshold_m=float("nan"))
    with pytest.raises(RoadGraphCleanupError, match="dangling_edge_threshold_m"):
        RoadGraphCleanupPolicy(dangling_edge_threshold_m=-1.0)
    with pytest.raises(RoadGraphCleanupError, match="max_prune_passes"):
        RoadGraphCleanupPolicy(max_prune_passes=0)

    graph = _dangling_chain_graph()
    with pytest.raises(RoadGraphCleanupError, match="cleanup edge limit exceeded"):
        RoadGraphCleaner(RoadGraphCleanupPolicy(max_edges=3)).cleanup(graph)
    with pytest.raises(RoadGraphCleanupError, match="cleanup node limit exceeded"):
        RoadGraphCleaner(RoadGraphCleanupPolicy(max_nodes=4)).cleanup(graph)


def test_empty_graph_cleanup_is_bounded_and_idempotent() -> None:
    graph = _build()

    result = RoadGraphCleaner().cleanup(graph)

    assert result.graph == graph
    assert result.diagnostics.input_node_count == 0
    assert result.diagnostics.output_node_count == 0
    assert result.diagnostics.input_edge_count == 0
    assert result.diagnostics.output_edge_count == 0
    assert result.diagnostics.prune_limit_reached is False
