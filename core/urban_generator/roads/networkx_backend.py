from __future__ import annotations

import copy
import math
from numbers import Real

import networkx as nx
from shapely.geometry import Point
from shapely.strtree import STRtree

from core.urban_generator.domain import (
    NetworkContractError,
    NetworkDistanceResult,
    NetworkGraphSnapshot,
    NetworkNodeRef,
    NetworkPath,
    NetworkPoint,
    NetworkSnapResult,
    WorkingCRS,
    require_max_distance_m,
    require_node_refs,
)


class NetworkXBackend:
    """NetworkX implementation of the backend-independent routing port."""

    version = "1"

    def __init__(
        self,
        *,
        graph: nx.Graph,
        snapshot_id: str,
        working_crs: WorkingCRS,
        x_attribute: str = "x_m",
        y_attribute: str = "y_m",
        length_attribute: str = "length_m",
    ) -> None:
        if not isinstance(graph, nx.Graph):
            raise NetworkContractError("graph must be a NetworkX graph")
        if not isinstance(working_crs, WorkingCRS):
            raise NetworkContractError("working_crs must be a validated WorkingCRS")
        self._x_attribute = _require_attribute_name(x_attribute, "x_attribute")
        self._y_attribute = _require_attribute_name(y_attribute, "y_attribute")
        self._length_attribute = _require_attribute_name(
            length_attribute,
            "length_attribute",
        )

        canonical_graph, positions = _canonical_graph_copy(
            graph,
            x_attribute=self._x_attribute,
            y_attribute=self._y_attribute,
            length_attribute=self._length_attribute,
        )
        self._graph = nx.freeze(canonical_graph)
        self._positions = positions
        self._node_ids = tuple(sorted(positions))
        self._points = tuple(
            Point(positions[node_id].x_m, positions[node_id].y_m)
            for node_id in self._node_ids
        )
        self._spatial_index = STRtree(self._points) if self._points else None
        self._snapshot = NetworkGraphSnapshot(
            snapshot_id=snapshot_id,
            working_crs=working_crs,
            node_count=self._graph.number_of_nodes(),
            edge_count=self._graph.number_of_edges(),
            directed=self._graph.is_directed(),
        )

    @property
    def snapshot(self) -> NetworkGraphSnapshot:
        return self._snapshot

    def node_point(self, node: NetworkNodeRef) -> NetworkPoint:
        """Convert one backend node reference to its working-CRS domain point."""

        node_id = self._require_known_node(node)
        return self._positions[node_id]

    def to_networkx(self) -> nx.Graph:
        """Return a detached mutable graph copy for adapter-level integrations."""

        return copy.deepcopy(self._graph.copy(as_view=False))

    def snap(
        self,
        point: NetworkPoint,
        *,
        max_distance_m: float,
    ) -> NetworkSnapResult | None:
        if not isinstance(point, NetworkPoint):
            raise NetworkContractError("point must be a NetworkPoint")
        limit = require_max_distance_m(max_distance_m)
        assert limit is not None
        if self._spatial_index is None:
            return None

        query_point = Point(point.x_m, point.y_m)
        candidate_indexes = self._spatial_index.query(
            query_point,
            predicate="dwithin",
            distance=limit,
        )
        candidates = tuple(
            (
                query_point.distance(self._points[int(index)]),
                self._node_ids[int(index)],
            )
            for index in candidate_indexes
        )
        if not candidates:
            return None
        distance_m, node_id = min(candidates, key=lambda item: (item[0], item[1]))
        return NetworkSnapResult(
            node=NetworkNodeRef(node_id=node_id),
            distance_m=float(distance_m),
        )

    def shortest_path(
        self,
        source: NetworkNodeRef,
        target: NetworkNodeRef,
        *,
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        source_id = self._require_known_node(source)
        target_id = self._require_known_node(target)
        limit = require_max_distance_m(max_distance_m)
        try:
            distance_m, node_ids = nx.single_source_dijkstra(
                self._graph,
                source_id,
                target_id,
                cutoff=limit,
                weight=self._length_attribute,
            )
        except nx.NetworkXNoPath:
            return None
        return NetworkPath(
            nodes=tuple(NetworkNodeRef(node_id=node_id) for node_id in node_ids),
            distance_m=float(distance_m),
        )

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        sources = require_node_refs(sources, field_name="sources")
        targets = require_node_refs(targets, field_name="targets", allow_empty=True)
        limit = require_max_distance_m(max_distance_m)

        source_ids = tuple(sorted({self._require_known_node(source) for source in sources}))
        target_ids = tuple(self._require_known_node(target) for target in targets)
        if not target_ids:
            return ()

        distances, paths = nx.multi_source_dijkstra(
            self._graph,
            source_ids,
            cutoff=limit,
            weight=self._length_attribute,
        )
        results: list[NetworkDistanceResult] = []
        for target, target_id in zip(targets, target_ids, strict=True):
            if target_id not in distances:
                continue
            path = paths[target_id]
            source_id = path[0]
            results.append(
                NetworkDistanceResult(
                    source=NetworkNodeRef(node_id=source_id),
                    target=target,
                    distance_m=float(distances[target_id]),
                )
            )
        return tuple(results)

    def _require_known_node(self, node: NetworkNodeRef) -> str:
        if not isinstance(node, NetworkNodeRef):
            raise NetworkContractError("node must be a NetworkNodeRef")
        if node.node_id not in self._positions:
            raise NetworkContractError(f"unknown network node: {node.node_id}")
        return node.node_id


def _canonical_graph_copy(
    graph: nx.Graph,
    *,
    x_attribute: str,
    y_attribute: str,
    length_attribute: str,
) -> tuple[nx.Graph, dict[str, NetworkPoint]]:
    canonical = _new_graph_like(graph)
    canonical.graph.update(copy.deepcopy(graph.graph))

    positions: dict[str, NetworkPoint] = {}
    node_rows: list[tuple[str, dict[str, object]]] = []
    for raw_node_id, raw_attributes in graph.nodes(data=True):
        if not isinstance(raw_node_id, str):
            raise NetworkContractError("NetworkX node ids must be strings")
        node_ref = NetworkNodeRef(node_id=raw_node_id)
        attributes = copy.deepcopy(dict(raw_attributes))
        x_m = _require_numeric_attribute(attributes, x_attribute, raw_node_id)
        y_m = _require_numeric_attribute(attributes, y_attribute, raw_node_id)
        point = NetworkPoint(x_m=x_m, y_m=y_m)
        positions[node_ref.node_id] = point
        attributes[x_attribute] = point.x_m
        attributes[y_attribute] = point.y_m
        node_rows.append((node_ref.node_id, attributes))

    for node_id, attributes in sorted(node_rows, key=lambda item: item[0]):
        canonical.add_node(node_id, **attributes)

    if graph.is_multigraph():
        edge_rows: list[tuple[str, str, object, dict[str, object]]] = []
        for raw_source, raw_target, key, raw_attributes in graph.edges(
            keys=True,
            data=True,
        ):
            source = _require_edge_node_id(raw_source)
            target = _require_edge_node_id(raw_target)
            attributes = copy.deepcopy(dict(raw_attributes))
            attributes[length_attribute] = _require_edge_length(
                attributes,
                length_attribute,
                source,
                target,
            )
            edge_rows.append((source, target, copy.deepcopy(key), attributes))
        edge_rows.sort(key=lambda row: _edge_sort_key(row[0], row[1], row[2]))
        for source, target, key, attributes in edge_rows:
            canonical.add_edge(source, target, key=key, **attributes)
    else:
        simple_rows: list[tuple[str, str, dict[str, object]]] = []
        for raw_source, raw_target, raw_attributes in graph.edges(data=True):
            source = _require_edge_node_id(raw_source)
            target = _require_edge_node_id(raw_target)
            attributes = copy.deepcopy(dict(raw_attributes))
            attributes[length_attribute] = _require_edge_length(
                attributes,
                length_attribute,
                source,
                target,
            )
            simple_rows.append((source, target, attributes))
        simple_rows.sort(key=lambda row: _edge_sort_key(row[0], row[1], None))
        for source, target, attributes in simple_rows:
            canonical.add_edge(source, target, **attributes)

    return canonical, positions


def _new_graph_like(graph: nx.Graph) -> nx.Graph:
    if graph.is_multigraph() and graph.is_directed():
        return nx.MultiDiGraph()
    if graph.is_multigraph():
        return nx.MultiGraph()
    if graph.is_directed():
        return nx.DiGraph()
    return nx.Graph()


def _require_attribute_name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NetworkContractError(f"{field_name} must be a non-empty string")
    return value


def _require_numeric_attribute(
    attributes: dict[str, object],
    attribute_name: str,
    node_id: str,
) -> float:
    if attribute_name not in attributes:
        raise NetworkContractError(
            f"network node {node_id} is missing required attribute {attribute_name!r}"
        )
    return _require_non_negative_or_signed_finite(
        attributes[attribute_name],
        f"network node {node_id} attribute {attribute_name!r}",
    )


def _require_edge_length(
    attributes: dict[str, object],
    attribute_name: str,
    source: str,
    target: str,
) -> float:
    if attribute_name not in attributes:
        raise NetworkContractError(
            f"network edge {source}->{target} is missing required attribute {attribute_name!r}"
        )
    length_m = _require_non_negative_or_signed_finite(
        attributes[attribute_name],
        f"network edge {source}->{target} attribute {attribute_name!r}",
    )
    if length_m < 0.0:
        raise NetworkContractError(
            f"network edge {source}->{target} attribute {attribute_name!r} must be non-negative"
        )
    return length_m


def _require_non_negative_or_signed_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise NetworkContractError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise NetworkContractError(f"{field_name} must be a finite number")
    return number


def _require_edge_node_id(value: object) -> str:
    if not isinstance(value, str):
        raise NetworkContractError("NetworkX edge endpoints must use string node ids")
    NetworkNodeRef(node_id=value)
    return value


def _edge_sort_key(source: str, target: str, key: object) -> tuple[str, str, str]:
    first, second = sorted((source, target))
    return first, second, repr(key)
