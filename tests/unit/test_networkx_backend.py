from __future__ import annotations

import networkx as nx
import pytest

from core.urban_generator.domain import (
    NetworkBackend,
    NetworkContractError,
    NetworkDistanceResult,
    NetworkNodeRef,
    NetworkPath,
    NetworkPoint,
    NetworkSnapResult,
    WorkingCRS,
)
from core.urban_generator.roads import NetworkXBackend


def _graph() -> nx.Graph:
    graph = nx.Graph()
    graph.add_node("a", x_m=0.0, y_m=0.0, source="fixed")
    graph.add_node("b", x_m=10.0, y_m=0.0, source="fixed")
    graph.add_node("c", x_m=20.0, y_m=0.0, source="generated")
    graph.add_node("d", x_m=100.0, y_m=100.0, source="fixed")
    graph.add_edge("a", "b", length_m=10.0, road_class="local")
    graph.add_edge("b", "c", length_m=10.0, road_class="local")
    graph.add_edge("a", "c", length_m=30.0, road_class="collector")
    return graph


def _backend(graph: nx.Graph | None = None) -> NetworkXBackend:
    return NetworkXBackend(
        graph=_graph() if graph is None else graph,
        snapshot_id="roads-snapshot-v1",
        working_crs=WorkingCRS(3857),
    )


def test_backend_satisfies_port_and_converts_graph_to_domain_snapshot() -> None:
    backend = _backend()

    assert isinstance(backend, NetworkBackend)
    assert backend.snapshot.snapshot_id == "roads-snapshot-v1"
    assert backend.snapshot.working_crs == WorkingCRS(3857)
    assert backend.snapshot.node_count == 4
    assert backend.snapshot.edge_count == 3
    assert backend.snapshot.directed is False
    assert backend.node_point(NetworkNodeRef("b")) == NetworkPoint(10.0, 0.0)


def test_backend_owns_frozen_copy_and_preserves_adapter_metadata() -> None:
    source = _graph()
    backend = _backend(source)

    source.remove_edge("a", "b")
    source.nodes["b"]["x_m"] = 999.0

    assert backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("c")) == NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("b"), NetworkNodeRef("c")),
        distance_m=20.0,
    )
    assert backend.node_point(NetworkNodeRef("b")) == NetworkPoint(10.0, 0.0)

    detached = backend.to_networkx()
    assert detached.nodes["a"]["source"] == "fixed"
    assert detached.edges["a", "b"]["road_class"] == "local"
    detached.remove_edge("a", "b")
    assert backend.snapshot.edge_count == 3


def test_snap_is_spatially_bounded_and_ties_use_canonical_node_id() -> None:
    backend = _backend()

    tied = backend.snap(NetworkPoint(5.0, 0.0), max_distance_m=5.0)
    missed = backend.snap(NetworkPoint(5.0, 0.0), max_distance_m=4.99)

    assert tied == NetworkSnapResult(node=NetworkNodeRef("a"), distance_m=5.0)
    assert missed is None


def test_shortest_path_uses_metric_edge_weight_and_optional_cutoff() -> None:
    backend = _backend()

    route = backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("c"))
    bounded_out = backend.shortest_path(
        NetworkNodeRef("a"),
        NetworkNodeRef("c"),
        max_distance_m=15.0,
    )
    disconnected = backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("d"))

    assert route == NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("b"), NetworkNodeRef("c")),
        distance_m=20.0,
    )
    assert bounded_out is None
    assert disconnected is None


def test_directed_graph_preserves_direction_in_snapshot_and_routing() -> None:
    graph = nx.DiGraph()
    graph.add_node("a", x_m=0.0, y_m=0.0)
    graph.add_node("b", x_m=10.0, y_m=0.0)
    graph.add_node("c", x_m=20.0, y_m=0.0)
    graph.add_edge("a", "b", length_m=10.0)
    graph.add_edge("b", "c", length_m=10.0)
    backend = _backend(graph)

    assert backend.snapshot.directed is True
    assert backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("c")) is not None
    assert backend.shortest_path(NetworkNodeRef("c"), NetworkNodeRef("a")) is None


def test_multigraph_routing_uses_shortest_parallel_edge() -> None:
    graph = nx.MultiGraph()
    graph.add_node("a", x_m=0.0, y_m=0.0)
    graph.add_node("b", x_m=10.0, y_m=0.0)
    graph.add_edge("a", "b", key="slow", length_m=10.0)
    graph.add_edge("a", "b", key="fast", length_m=4.0)
    backend = _backend(graph)

    assert backend.snapshot.edge_count == 2
    assert backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("b")) == NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("b")),
        distance_m=4.0,
    )


def test_multi_source_distances_preserve_target_order_and_nearest_source() -> None:
    backend = _backend()

    results = backend.multi_source_distances(
        (NetworkNodeRef("c"), NetworkNodeRef("a")),
        (NetworkNodeRef("b"), NetworkNodeRef("c"), NetworkNodeRef("d")),
    )

    assert results == (
        NetworkDistanceResult(
            source=NetworkNodeRef("a"),
            target=NetworkNodeRef("b"),
            distance_m=10.0,
        ),
        NetworkDistanceResult(
            source=NetworkNodeRef("c"),
            target=NetworkNodeRef("c"),
            distance_m=0.0,
        ),
    )


def test_multi_source_cutoff_omits_targets_outside_network_distance() -> None:
    backend = _backend()

    results = backend.multi_source_distances(
        (NetworkNodeRef("a"),),
        (NetworkNodeRef("a"), NetworkNodeRef("b"), NetworkNodeRef("c")),
        max_distance_m=10.0,
    )

    assert tuple(item.target.node_id for item in results) == ("a", "b")


def test_empty_graph_has_valid_snapshot_and_snap_returns_none() -> None:
    backend = _backend(nx.Graph())

    assert backend.snapshot.node_count == 0
    assert backend.snapshot.edge_count == 0
    assert backend.snap(NetworkPoint(0.0, 0.0), max_distance_m=100.0) is None


def test_backend_rejects_invalid_node_and_edge_adapter_data() -> None:
    non_string_node = nx.Graph()
    non_string_node.add_node(1, x_m=0.0, y_m=0.0)
    with pytest.raises(NetworkContractError, match="node ids must be strings"):
        _backend(non_string_node)

    missing_position = nx.Graph()
    missing_position.add_node("a", x_m=0.0)
    with pytest.raises(NetworkContractError, match="missing required attribute 'y_m'"):
        _backend(missing_position)

    missing_length = nx.Graph()
    missing_length.add_node("a", x_m=0.0, y_m=0.0)
    missing_length.add_node("b", x_m=1.0, y_m=0.0)
    missing_length.add_edge("a", "b")
    with pytest.raises(NetworkContractError, match="missing required attribute 'length_m'"):
        _backend(missing_length)

    negative_length = nx.Graph()
    negative_length.add_node("a", x_m=0.0, y_m=0.0)
    negative_length.add_node("b", x_m=1.0, y_m=0.0)
    negative_length.add_edge("a", "b", length_m=-1.0)
    with pytest.raises(NetworkContractError, match="must be non-negative"):
        _backend(negative_length)


def test_backend_rejects_unknown_domain_node_references() -> None:
    backend = _backend()

    with pytest.raises(NetworkContractError, match="unknown network node"):
        backend.node_point(NetworkNodeRef("missing"))
    with pytest.raises(NetworkContractError, match="unknown network node"):
        backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("missing"))
    with pytest.raises(NetworkContractError, match="unknown network node"):
        backend.multi_source_distances(
            (NetworkNodeRef("missing"),),
            (NetworkNodeRef("a"),),
        )
