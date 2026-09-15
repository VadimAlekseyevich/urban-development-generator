import uuid

import pytest
from shapely.geometry import LineString

from core.urban_generator.domain import NetworkNodeRef, WorkingCRS, WorldStateContract
from core.urban_generator.domain.crs import CRSContractError
from core.urban_generator.roads import (
    NetworkXBackend,
    NodedRoad,
    RoadGraphBuilder,
    RoadGraphBuildError,
    RoadGraphInput,
    SemanticNoder,
    SemanticRoad,
)

WORKING_SRID = 3857
GENERATED_RUN_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _fixed(road: NodedRoad) -> RoadGraphInput:
    return RoadGraphInput(road=road, state=WorldStateContract.fixed_source())


def _generated(road: NodedRoad) -> RoadGraphInput:
    return RoadGraphInput(
        road=road,
        state=WorldStateContract.generated_for(GENERATED_RUN_ID),
    )


def _simple_road(
    road_id: str,
    coordinates: tuple[tuple[float, float], ...],
) -> NodedRoad:
    return NodedRoad(road_id=road_id, parts=(LineString(coordinates),))


def test_graph_builder_consumes_semantic_noding_and_preserves_world_state() -> None:
    noded = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            SemanticRoad(road_id="source-road", geometry=LineString(((0, 0), (10, 0)))),
            SemanticRoad(road_id="generated-road", geometry=LineString(((5, -5), (5, 5)))),
        )
    )
    by_id = {road.road_id: road for road in noded.roads}

    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            _fixed(by_id["source-road"]),
            _generated(by_id["generated-road"]),
        )
    )

    assert graph.working_crs == WorkingCRS(srid=WORKING_SRID)
    assert graph.diagnostics.node_count == 5
    assert graph.diagnostics.edge_count == 4
    assert graph.diagnostics.total_length_m == pytest.approx(20.0)
    assert graph.diagnostics.source_edge_count == 2
    assert graph.diagnostics.fixed_edge_count == 2
    assert graph.diagnostics.component_count == 1
    assert graph.diagnostics.components[0].node_count == 5
    assert graph.diagnostics.components[0].edge_count == 4

    junction = next(node for node in graph.nodes if node.point.x_m == 5 and node.point.y_m == 0)
    assert junction.is_source is True
    assert junction.is_fixed is True

    generated_endpoint = next(
        node for node in graph.nodes if node.point.x_m == 5 and node.point.y_m == -5
    )
    assert generated_endpoint.is_source is False
    assert generated_endpoint.is_fixed is False

    generated_edges = tuple(edge for edge in graph.edges if edge.road_id == "generated-road")
    assert all(edge.is_source is False for edge in generated_edges)
    assert all(edge.is_fixed is False for edge in generated_edges)
    assert all(edge.state.run_id == GENERATED_RUN_ID for edge in generated_edges)


def test_graph_builder_preserves_part_direction_and_metric_length() -> None:
    road = NodedRoad(
        road_id="directed-by-geometry",
        parts=(LineString(((0, 0), (3, 4))), LineString(((3, 4), (6, 4)))),
    )

    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build((_fixed(road),))

    assert [edge.length_m for edge in graph.edges] == [5.0, 3.0]
    assert graph.edges[0].source == NetworkNodeRef("node:00000000")
    assert graph.edges[0].target == NetworkNodeRef("node:00000001")
    assert tuple(graph.edges[0].geometry.coords) == ((0.0, 0.0), (3.0, 4.0))
    assert graph.edges[1].source == NetworkNodeRef("node:00000001")
    assert graph.edges[1].target == NetworkNodeRef("node:00000002")


def test_graph_identifiers_are_deterministic_independent_of_input_order() -> None:
    alpha = _fixed(_simple_road("alpha", ((0, 0), (2, 0))))
    beta = _generated(_simple_road("beta", ((2, 0), (4, 0))))
    builder = RoadGraphBuilder(working_srid=WORKING_SRID)

    forward = builder.build((alpha, beta))
    reversed_input = builder.build((beta, alpha))

    assert forward.nodes == reversed_input.nodes
    assert forward.edges == reversed_input.edges
    assert forward.diagnostics == reversed_input.diagnostics


def test_connected_component_diagnostics_are_complete_and_deterministic() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            _fixed(_simple_road("a", ((0, 0), (3, 0)))),
            _generated(_simple_road("b", ((10, 0), (10, 4)))),
        )
    )

    assert graph.diagnostics.component_count == 2
    assert graph.diagnostics.total_length_m == 7.0
    assert tuple(
        (
            component.component_id,
            component.node_count,
            component.edge_count,
            component.total_length_m,
            component.fixed_edge_count,
        )
        for component in graph.diagnostics.components
    ) == (
        ("node:00000000", 2, 1, 3.0, 1),
        ("node:00000002", 2, 1, 4.0, 0),
    )


def test_networkx_adapter_preserves_parallel_graph_edges_and_routing_cost() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            _fixed(_simple_road("primary", ((0, 0), (3, 4)))),
            _generated(
                NodedRoad(
                    road_id="parallel",
                    parts=(LineString(((0, 0), (0, 4), (3, 4))),),
                )
            ),
        )
    )

    backend = NetworkXBackend.from_road_graph(graph, snapshot_id="built-roads")
    path = backend.shortest_path(NetworkNodeRef("node:00000000"), NetworkNodeRef("node:00000001"))

    assert backend.snapshot.node_count == 2
    assert backend.snapshot.edge_count == 2
    assert backend.snapshot.directed is False
    assert path is not None
    assert path.distance_m == 5.0


def test_graph_builder_rejects_duplicate_roads_non_metric_crs_and_3d_parts() -> None:
    road = _simple_road("duplicate", ((0, 0), (1, 0)))
    builder = RoadGraphBuilder(working_srid=WORKING_SRID)

    with pytest.raises(RoadGraphBuildError, match="duplicate road_id"):
        builder.build((_fixed(road), _fixed(road)))

    with pytest.raises(CRSContractError, match="not projected"):
        RoadGraphBuilder(working_srid=4326)

    road_3d = NodedRoad(
        road_id="3d",
        parts=(LineString(((0, 0, 1), (1, 0, 1))),),
    )
    with pytest.raises(RoadGraphBuildError, match="must be 2D"):
        builder.build((_fixed(road_3d),))


def test_graph_builder_enforces_configured_node_and_edge_limits() -> None:
    two_part_road = NodedRoad(
        road_id="bounded",
        parts=(LineString(((0, 0), (1, 0))), LineString(((1, 0), (2, 0)))),
    )

    with pytest.raises(RoadGraphBuildError, match="graph edge limit exceeded: 2 > 1"):
        RoadGraphBuilder(working_srid=WORKING_SRID, max_edges=1).build(
            (_fixed(two_part_road),)
        )

    with pytest.raises(RoadGraphBuildError, match="graph node limit exceeded: 2 > 1"):
        RoadGraphBuilder(working_srid=WORKING_SRID, max_nodes=1).build(
            (_fixed(two_part_road),)
        )


def test_empty_graph_build_returns_empty_diagnostics() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(())

    assert graph.nodes == ()
    assert graph.edges == ()
    assert graph.diagnostics.node_count == 0
    assert graph.diagnostics.edge_count == 0
    assert graph.diagnostics.component_count == 0
    assert graph.diagnostics.components == ()
