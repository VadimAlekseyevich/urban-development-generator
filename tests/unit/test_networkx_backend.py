import networkx as nx
import pytest

from core.urban_generator.domain import (
    NetworkBackend,
    NetworkDistanceResult,
    NetworkNodeRef,
    NetworkPath,
    NetworkPoint,
    NetworkRoutingAlgorithm,
    NetworkSnapResult,
    WorkingCRS,
)
from core.urban_generator.roads import (
    DEFAULT_MAX_ROUTING_VISITED_NODES,
    NetworkXBackend,
    NetworkXBackendError,
)


def _graph() -> nx.Graph:
    graph = nx.Graph()
    graph.add_node("a", x_m=0.0, y_m=0.0)
    graph.add_node("b", x_m=5.0, y_m=0.0)
    graph.add_node("c", x_m=10.0, y_m=0.0)
    graph.add_node("d", x_m=10.0, y_m=4.0)
    graph.add_node("e", x_m=30.0, y_m=0.0)
    graph.add_edge("a", "b", length_m=5.0)
    graph.add_edge("b", "c", length_m=5.0)
    graph.add_edge("a", "c", length_m=20.0)
    graph.add_edge("c", "d", length_m=4.0)
    return graph


def _backend(
    graph: nx.Graph | None = None,
    *,
    max_routing_visited_nodes: int = DEFAULT_MAX_ROUTING_VISITED_NODES,
) -> NetworkXBackend:
    return NetworkXBackend(
        graph or _graph(),
        snapshot_id="roads-v1",
        working_crs=WorkingCRS(srid=3857),
        max_routing_visited_nodes=max_routing_visited_nodes,
    )


def test_networkx_backend_satisfies_domain_protocol_and_builds_snapshot() -> None:
    backend = _backend()

    assert isinstance(backend, NetworkBackend)
    assert backend.snapshot.snapshot_id == "roads-v1"
    assert backend.snapshot.working_crs == WorkingCRS(srid=3857)
    assert backend.snapshot.node_count == 5
    assert backend.snapshot.edge_count == 4
    assert backend.snapshot.directed is False


def test_backend_owns_snapshot_copy_instead_of_mutating_with_source_graph() -> None:
    graph = _graph()
    backend = _backend(graph)

    graph.add_edge("c", "e", length_m=1.0)

    assert backend.snapshot.edge_count == 4
    assert backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("e")) is None


def test_snap_returns_nearest_node_with_metric_bound_and_deterministic_ties() -> None:
    backend = _backend()

    result = backend.snap(NetworkPoint(x_m=9.0, y_m=0.0), max_distance_m=2.0)
    missed = backend.snap(NetworkPoint(x_m=9.0, y_m=0.0), max_distance_m=0.5)
    tie = backend.snap(NetworkPoint(x_m=2.5, y_m=0.0), max_distance_m=3.0)

    assert result == NetworkSnapResult(node=NetworkNodeRef("c"), distance_m=1.0)
    assert missed is None
    assert tie == NetworkSnapResult(node=NetworkNodeRef("a"), distance_m=2.5)


def test_shortest_path_uses_length_m_and_honours_cutoff() -> None:
    backend = _backend()

    path = backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("c"))
    bounded_out = backend.shortest_path(
        NetworkNodeRef("a"),
        NetworkNodeRef("c"),
        max_distance_m=9.0,
    )

    assert path == NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("b"), NetworkNodeRef("c")),
        distance_m=10.0,
    )
    assert bounded_out is None


def test_astar_matches_dijkstra_and_honours_metric_cutoff() -> None:
    backend = _backend()

    dijkstra = backend.shortest_path(
        NetworkNodeRef("a"),
        NetworkNodeRef("d"),
        algorithm=NetworkRoutingAlgorithm.DIJKSTRA,
    )
    astar = backend.shortest_path(
        NetworkNodeRef("a"),
        NetworkNodeRef("d"),
        algorithm=NetworkRoutingAlgorithm.ASTAR,
    )
    bounded_out = backend.shortest_path(
        NetworkNodeRef("a"),
        NetworkNodeRef("d"),
        algorithm=NetworkRoutingAlgorithm.ASTAR,
        max_distance_m=13.0,
    )

    expected = NetworkPath(
        nodes=(
            NetworkNodeRef("a"),
            NetworkNodeRef("b"),
            NetworkNodeRef("c"),
            NetworkNodeRef("d"),
        ),
        distance_m=14.0,
    )
    assert dijkstra == expected
    assert astar == expected
    assert bounded_out is None


def test_astar_scales_heuristic_for_edges_shorter_than_straight_line_distance() -> None:
    graph = nx.Graph()
    graph.add_node("a", x_m=0.0, y_m=0.0)
    graph.add_node("b", x_m=100.0, y_m=0.0)
    graph.add_node("c", x_m=200.0, y_m=0.0)
    graph.add_edge("a", "b", length_m=1.0)
    graph.add_edge("b", "c", length_m=1.0)
    graph.add_edge("a", "c", length_m=3.0)
    backend = _backend(graph)

    path = backend.shortest_path(
        NetworkNodeRef("a"),
        NetworkNodeRef("c"),
        algorithm=NetworkRoutingAlgorithm.ASTAR,
    )

    assert path == NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("b"), NetworkNodeRef("c")),
        distance_m=2.0,
    )


def test_multi_source_shortest_path_selects_nearest_source() -> None:
    backend = _backend()

    path = backend.multi_source_shortest_path(
        (NetworkNodeRef("a"), NetworkNodeRef("d")),
        NetworkNodeRef("b"),
        algorithm=NetworkRoutingAlgorithm.ASTAR,
    )

    assert path == NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("b")),
        distance_m=5.0,
    )


def test_multi_source_shortest_path_breaks_equal_distance_source_ties_stably() -> None:
    graph = nx.Graph()
    graph.add_node("a", x_m=-1.0, y_m=0.0)
    graph.add_node("z", x_m=1.0, y_m=0.0)
    graph.add_node("target", x_m=0.0, y_m=0.0)
    graph.add_edge("a", "target", length_m=1.0)
    graph.add_edge("z", "target", length_m=1.0)
    backend = _backend(graph)

    first = backend.multi_source_shortest_path(
        (NetworkNodeRef("z"), NetworkNodeRef("a")),
        NetworkNodeRef("target"),
    )
    second = backend.multi_source_shortest_path(
        (NetworkNodeRef("a"), NetworkNodeRef("z")),
        NetworkNodeRef("target"),
        algorithm=NetworkRoutingAlgorithm.ASTAR,
    )

    expected = NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("target")),
        distance_m=1.0,
    )
    assert first == expected
    assert second == expected


def test_shortest_path_respects_directed_topology() -> None:
    graph = nx.DiGraph()
    graph.add_node("a", x_m=0.0, y_m=0.0)
    graph.add_node("b", x_m=1.0, y_m=0.0)
    graph.add_node("c", x_m=2.0, y_m=0.0)
    graph.add_edge("a", "b", length_m=1.0)
    graph.add_edge("b", "c", length_m=1.0)
    backend = _backend(graph)

    forward = backend.shortest_path(
        NetworkNodeRef("a"),
        NetworkNodeRef("c"),
        algorithm=NetworkRoutingAlgorithm.ASTAR,
    )
    reverse = backend.shortest_path(NetworkNodeRef("c"), NetworkNodeRef("a"))

    assert forward == NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("b"), NetworkNodeRef("c")),
        distance_m=2.0,
    )
    assert reverse is None
    assert backend.snapshot.directed is True


def test_multi_source_distances_returns_nearest_source_for_reachable_targets() -> None:
    backend = _backend()

    results = backend.multi_source_distances(
        (NetworkNodeRef("a"), NetworkNodeRef("d")),
        (NetworkNodeRef("b"), NetworkNodeRef("c"), NetworkNodeRef("e")),
    )

    assert results == (
        NetworkDistanceResult(
            source=NetworkNodeRef("a"),
            target=NetworkNodeRef("b"),
            distance_m=5.0,
        ),
        NetworkDistanceResult(
            source=NetworkNodeRef("d"),
            target=NetworkNodeRef("c"),
            distance_m=4.0,
        ),
    )


def test_multi_source_distances_honours_network_distance_cutoff() -> None:
    backend = _backend()

    results = backend.multi_source_distances(
        (NetworkNodeRef("a"),),
        (NetworkNodeRef("a"), NetworkNodeRef("b"), NetworkNodeRef("c")),
        max_distance_m=5.0,
    )

    assert results == (
        NetworkDistanceResult(
            source=NetworkNodeRef("a"),
            target=NetworkNodeRef("a"),
            distance_m=0.0,
        ),
        NetworkDistanceResult(
            source=NetworkNodeRef("a"),
            target=NetworkNodeRef("b"),
            distance_m=5.0,
        ),
    )


def test_multi_source_distances_breaks_source_ties_independent_of_input_order() -> None:
    graph = nx.Graph()
    graph.add_node("a", x_m=-1.0, y_m=0.0)
    graph.add_node("z", x_m=1.0, y_m=0.0)
    graph.add_node("target", x_m=0.0, y_m=0.0)
    graph.add_edge("a", "target", length_m=1.0)
    graph.add_edge("z", "target", length_m=1.0)
    backend = _backend(graph)

    results = backend.multi_source_distances(
        (NetworkNodeRef("z"), NetworkNodeRef("a")),
        (NetworkNodeRef("target"),),
    )

    assert results == (
        NetworkDistanceResult(
            source=NetworkNodeRef("a"),
            target=NetworkNodeRef("target"),
            distance_m=1.0,
        ),
    )


def test_routing_search_is_bounded_by_visited_node_limit() -> None:
    graph = nx.path_graph(("a", "b", "c"))
    for index, node_id in enumerate(("a", "b", "c")):
        graph.nodes[node_id]["x_m"] = float(index)
        graph.nodes[node_id]["y_m"] = 0.0
    for source_id, target_id in graph.edges:
        graph.edges[source_id, target_id]["length_m"] = 1.0
    backend = _backend(graph, max_routing_visited_nodes=1)

    with pytest.raises(NetworkXBackendError, match="routing visit limit exceeded"):
        backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("c"))


def test_backend_rejects_graphs_without_metric_node_coordinates() -> None:
    graph = nx.Graph()
    graph.add_node("a", x_m=0.0)

    with pytest.raises(NetworkXBackendError, match="node 'a' y_m must be a finite number"):
        _backend(graph)


def test_backend_rejects_non_string_node_ids_and_invalid_edge_lengths() -> None:
    invalid_node_graph = nx.Graph()
    invalid_node_graph.add_node(1, x_m=0.0, y_m=0.0)

    with pytest.raises(NetworkXBackendError, match="graph node identifiers must be strings"):
        _backend(invalid_node_graph)

    invalid_edge_graph = nx.Graph()
    invalid_edge_graph.add_node("a", x_m=0.0, y_m=0.0)
    invalid_edge_graph.add_node("b", x_m=1.0, y_m=0.0)
    invalid_edge_graph.add_edge("a", "b", length_m=-1.0)

    with pytest.raises(NetworkXBackendError, match="length_m must be non-negative"):
        _backend(invalid_edge_graph)


def test_backend_rejects_references_outside_its_snapshot() -> None:
    backend = _backend()

    with pytest.raises(NetworkXBackendError, match="is not part of snapshot"):
        backend.shortest_path(NetworkNodeRef("missing"), NetworkNodeRef("a"))


def test_backend_rejects_untyped_algorithm_and_invalid_routing_limit() -> None:
    backend = _backend()

    with pytest.raises(NetworkXBackendError, match="NetworkRoutingAlgorithm"):
        backend.shortest_path(
            NetworkNodeRef("a"),
            NetworkNodeRef("b"),
            algorithm="astar",  # type: ignore[arg-type]
        )

    with pytest.raises(NetworkXBackendError, match="max_routing_visited_nodes"):
        _backend(max_routing_visited_nodes=0)


def test_backend_enforces_configured_snap_target_limit() -> None:
    with pytest.raises(NetworkXBackendError, match="snap target limit exceeded: 5 > 4"):
        NetworkXBackend(
            _graph(),
            snapshot_id="roads-v1",
            working_crs=WorkingCRS(srid=3857),
            max_snap_targets=4,
        )
