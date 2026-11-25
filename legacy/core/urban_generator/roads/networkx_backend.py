from __future__ import annotations

from heapq import heappop, heappush
from math import hypot, isfinite

import networkx as nx

from core.urban_generator.domain import (
    NetworkBackend,
    NetworkContractError,
    NetworkDistanceResult,
    NetworkGraphSnapshot,
    NetworkNodeRef,
    NetworkPath,
    NetworkPoint,
    NetworkRoutingAlgorithm,
    NetworkSnapResult,
    WorkingCRS,
    require_max_distance_m,
    require_node_refs,
    require_routing_algorithm,
)
from core.urban_generator.roads.road_graph import RoadGraph
from core.urban_generator.roads.spatial_snapping import (
    DEFAULT_MAX_SNAP_TARGETS,
    SpatialSnapIndex,
    SpatialSnappingError,
    SpatialSnapTarget,
)

NODE_X_ATTRIBUTE = "x_m"
NODE_Y_ATTRIBUTE = "y_m"
NODE_SOURCE_ATTRIBUTE = "is_source"
NODE_FIXED_ATTRIBUTE = "is_fixed"
EDGE_LENGTH_ATTRIBUTE = "length_m"
EDGE_ROAD_ID_ATTRIBUTE = "road_id"
EDGE_SOURCE_ATTRIBUTE = "is_source"
EDGE_FIXED_ATTRIBUTE = "is_fixed"
DEFAULT_MAX_ROUTING_VISITED_NODES = 1_000_000


class NetworkXBackendError(NetworkContractError):
    """Raised when a NetworkX graph cannot satisfy the network domain contract."""


class NetworkXBackend(NetworkBackend):
    """NetworkX adapter for the backend-independent routing domain port.

    The adapter owns an immutable copy of the supplied graph. Node identifiers stay
    backend-independent strings; node coordinates and edge costs are expressed in metres
    in the supplied working CRS. Node snapping delegates to the reusable STRtree-backed
    spatial index instead of scanning all graph nodes. Routing uses deterministic bounded
    Dijkstra/A* searches rather than exposing NetworkX-specific result or exception types.
    """

    def __init__(
        self,
        graph: nx.Graph,
        *,
        snapshot_id: str,
        working_crs: WorkingCRS,
        max_snap_targets: int = DEFAULT_MAX_SNAP_TARGETS,
        max_routing_visited_nodes: int = DEFAULT_MAX_ROUTING_VISITED_NODES,
    ) -> None:
        if not isinstance(graph, nx.Graph):
            raise NetworkXBackendError("graph must be a NetworkX graph")
        if not isinstance(working_crs, WorkingCRS):
            raise NetworkXBackendError("working_crs must be a validated WorkingCRS")
        _require_positive_int(
            max_routing_visited_nodes,
            field_name="max_routing_visited_nodes",
        )

        graph_copy = graph.copy(as_view=False)
        positions = self._validate_graph(graph_copy)
        self._positions = {node.node_id: position for node, position in positions}
        self._heuristic_scale = _build_heuristic_scale(graph_copy, self._positions)
        self._max_routing_visited_nodes = max_routing_visited_nodes
        try:
            self._snap_index = SpatialSnapIndex(
                targets=tuple(
                    SpatialSnapTarget(target_id=node.node_id, point=position)
                    for node, position in positions
                ),
                working_srid=working_crs.srid,
                max_targets=max_snap_targets,
            )
        except SpatialSnappingError as exc:
            raise NetworkXBackendError(str(exc)) from exc

        self._graph = nx.freeze(graph_copy)
        self._snapshot = NetworkGraphSnapshot(
            snapshot_id=snapshot_id,
            working_crs=working_crs,
            node_count=self._graph.number_of_nodes(),
            edge_count=self._graph.number_of_edges(),
            directed=self._graph.is_directed(),
        )

    @classmethod
    def from_road_graph(
        cls,
        road_graph: RoadGraph,
        *,
        snapshot_id: str,
        max_snap_targets: int = DEFAULT_MAX_SNAP_TARGETS,
        max_routing_visited_nodes: int = DEFAULT_MAX_ROUTING_VISITED_NODES,
    ) -> NetworkXBackend:
        """Adapt a backend-independent road graph without collapsing parallel edges."""

        if not isinstance(road_graph, RoadGraph):
            raise NetworkXBackendError("road_graph must be a RoadGraph")

        adapter_graph = nx.MultiGraph()
        for node in road_graph.nodes:
            adapter_graph.add_node(
                node.node.node_id,
                **{
                    NODE_X_ATTRIBUTE: node.point.x_m,
                    NODE_Y_ATTRIBUTE: node.point.y_m,
                    NODE_SOURCE_ATTRIBUTE: node.is_source,
                    NODE_FIXED_ATTRIBUTE: node.is_fixed,
                },
            )
        for edge in road_graph.edges:
            adapter_graph.add_edge(
                edge.source.node_id,
                edge.target.node_id,
                key=edge.edge_id,
                **{
                    EDGE_LENGTH_ATTRIBUTE: edge.length_m,
                    EDGE_ROAD_ID_ATTRIBUTE: edge.road_id,
                    EDGE_SOURCE_ATTRIBUTE: edge.is_source,
                    EDGE_FIXED_ATTRIBUTE: edge.is_fixed,
                },
            )

        return cls(
            adapter_graph,
            snapshot_id=snapshot_id,
            working_crs=road_graph.working_crs,
            max_snap_targets=max_snap_targets,
            max_routing_visited_nodes=max_routing_visited_nodes,
        )

    @property
    def snapshot(self) -> NetworkGraphSnapshot:
        return self._snapshot

    def snap(
        self,
        point: NetworkPoint,
        *,
        max_distance_m: float,
    ) -> NetworkSnapResult | None:
        if not isinstance(point, NetworkPoint):
            raise NetworkXBackendError("point must be a NetworkPoint")

        limit = require_max_distance_m(max_distance_m)
        assert limit is not None
        try:
            match = self._snap_index.snap(point, tolerance_m=limit)
        except SpatialSnappingError as exc:
            raise NetworkXBackendError(str(exc)) from exc
        if match is None:
            return None
        return NetworkSnapResult(
            node=NetworkNodeRef(node_id=match.target.target_id),
            distance_m=match.distance_m,
        )

    def shortest_path(
        self,
        source: NetworkNodeRef,
        target: NetworkNodeRef,
        *,
        algorithm: NetworkRoutingAlgorithm = NetworkRoutingAlgorithm.DIJKSTRA,
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        source_id = self._require_known_node(source, field_name="source")
        target_id = self._require_known_node(target, field_name="target")
        selected_algorithm = require_routing_algorithm(algorithm)
        limit = require_max_distance_m(max_distance_m)

        distances, _, parents = self._search(
            source_ids=(source_id,),
            target_ids=frozenset((target_id,)),
            algorithm=selected_algorithm,
            heuristic_target_id=target_id,
            max_distance_m=limit,
        )
        if target_id not in distances:
            return None
        return NetworkPath(
            nodes=self._reconstruct_path(parents, target_id),
            distance_m=distances[target_id],
        )

    def multi_source_shortest_path(
        self,
        sources: tuple[NetworkNodeRef, ...],
        target: NetworkNodeRef,
        *,
        algorithm: NetworkRoutingAlgorithm = NetworkRoutingAlgorithm.DIJKSTRA,
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        sources = require_node_refs(sources, field_name="sources")
        target_id = self._require_known_node(target, field_name="target")
        source_ids = tuple(
            self._require_known_node(source, field_name="source") for source in sources
        )
        selected_algorithm = require_routing_algorithm(algorithm)
        limit = require_max_distance_m(max_distance_m)

        distances, _, parents = self._search(
            source_ids=source_ids,
            target_ids=frozenset((target_id,)),
            algorithm=selected_algorithm,
            heuristic_target_id=target_id,
            max_distance_m=limit,
        )
        if target_id not in distances:
            return None
        return NetworkPath(
            nodes=self._reconstruct_path(parents, target_id),
            distance_m=distances[target_id],
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
        source_ids = tuple(
            self._require_known_node(source, field_name="source") for source in sources
        )
        target_ids = tuple(
            self._require_known_node(target, field_name="target") for target in targets
        )
        if not target_ids:
            return ()
        limit = require_max_distance_m(max_distance_m)

        distances, origins, _ = self._search(
            source_ids=source_ids,
            target_ids=frozenset(target_ids),
            algorithm=NetworkRoutingAlgorithm.DIJKSTRA,
            heuristic_target_id=None,
            max_distance_m=limit,
        )

        results: list[NetworkDistanceResult] = []
        for target, target_id in zip(targets, target_ids, strict=True):
            if target_id not in distances:
                continue
            results.append(
                NetworkDistanceResult(
                    source=NetworkNodeRef(node_id=origins[target_id]),
                    target=target,
                    distance_m=distances[target_id],
                )
            )
        return tuple(results)

    def _search(
        self,
        *,
        source_ids: tuple[str, ...],
        target_ids: frozenset[str],
        algorithm: NetworkRoutingAlgorithm,
        heuristic_target_id: str | None,
        max_distance_m: float | None,
    ) -> tuple[dict[str, float], dict[str, str], dict[str, str | None]]:
        ordered_sources = tuple(sorted(set(source_ids)))
        best: dict[str, tuple[float, str, str | None]] = {}
        queue: list[tuple[float, float, str, str, str]] = []

        for source_id in ordered_sources:
            best[source_id] = (0.0, source_id, None)
            heuristic = self._heuristic(
                source_id,
                heuristic_target_id,
                algorithm=algorithm,
            )
            heappush(queue, (heuristic, 0.0, source_id, source_id, ""))

        settled: set[str] = set()
        distances: dict[str, float] = {}
        origins: dict[str, str] = {}
        parents: dict[str, str | None] = {}
        remaining_targets = set(target_ids)

        while queue:
            _, distance_m, origin_id, node_id, parent_marker = heappop(queue)
            current = best.get(node_id)
            if current is None:
                continue
            current_parent_marker = current[2] or ""
            if (distance_m, origin_id, parent_marker) != (
                current[0],
                current[1],
                current_parent_marker,
            ):
                continue
            if node_id in settled:
                continue

            settled.add(node_id)
            if len(settled) > self._max_routing_visited_nodes:
                raise NetworkXBackendError(
                    "routing visit limit exceeded: "
                    f"{len(settled)} > {self._max_routing_visited_nodes}"
                )
            distances[node_id] = distance_m
            origins[node_id] = origin_id
            parents[node_id] = current[2]

            if node_id in remaining_targets:
                remaining_targets.remove(node_id)
                if not remaining_targets:
                    break

            for neighbor_id in sorted(self._graph.neighbors(node_id)):
                if neighbor_id in settled:
                    continue
                candidate_distance = distance_m + self._edge_weight(node_id, neighbor_id)
                if max_distance_m is not None and candidate_distance > max_distance_m:
                    continue

                candidate = (candidate_distance, origin_id, node_id)
                existing = best.get(neighbor_id)
                if existing is not None:
                    existing_key = (existing[0], existing[1], existing[2] or "")
                    if candidate >= existing_key:
                        continue

                best[neighbor_id] = (candidate_distance, origin_id, node_id)
                heuristic = self._heuristic(
                    neighbor_id,
                    heuristic_target_id,
                    algorithm=algorithm,
                )
                heappush(
                    queue,
                    (
                        candidate_distance + heuristic,
                        candidate_distance,
                        origin_id,
                        neighbor_id,
                        node_id,
                    ),
                )

        return distances, origins, parents

    def _heuristic(
        self,
        node_id: str,
        target_id: str | None,
        *,
        algorithm: NetworkRoutingAlgorithm,
    ) -> float:
        if algorithm is NetworkRoutingAlgorithm.DIJKSTRA or target_id is None:
            return 0.0
        node = self._positions[node_id]
        target = self._positions[target_id]
        return self._heuristic_scale * hypot(
            node.x_m - target.x_m,
            node.y_m - target.y_m,
        )

    def _edge_weight(self, source_id: str, target_id: str) -> float:
        edge_data = self._graph.get_edge_data(source_id, target_id)
        assert edge_data is not None
        if self._graph.is_multigraph():
            return min(
                float(attributes[EDGE_LENGTH_ATTRIBUTE])
                for attributes in edge_data.values()
            )
        return float(edge_data[EDGE_LENGTH_ATTRIBUTE])

    @staticmethod
    def _reconstruct_path(
        parents: dict[str, str | None],
        target_id: str,
    ) -> tuple[NetworkNodeRef, ...]:
        node_id: str | None = target_id
        path: list[NetworkNodeRef] = []
        while node_id is not None:
            path.append(NetworkNodeRef(node_id=node_id))
            node_id = parents[node_id]
        path.reverse()
        return tuple(path)

    @staticmethod
    def _validate_graph(
        graph: nx.Graph,
    ) -> tuple[tuple[NetworkNodeRef, NetworkPoint], ...]:
        positions: list[tuple[NetworkNodeRef, NetworkPoint]] = []
        for node_id, attributes in graph.nodes(data=True):
            if not isinstance(node_id, str):
                raise NetworkXBackendError("graph node identifiers must be strings")
            try:
                node = NetworkNodeRef(node_id=node_id)
            except NetworkContractError as exc:
                raise NetworkXBackendError(str(exc)) from exc

            x_m = _require_finite_number(
                attributes.get(NODE_X_ATTRIBUTE),
                field_name=f"node {node_id!r} {NODE_X_ATTRIBUTE}",
            )
            y_m = _require_finite_number(
                attributes.get(NODE_Y_ATTRIBUTE),
                field_name=f"node {node_id!r} {NODE_Y_ATTRIBUTE}",
            )
            positions.append((node, NetworkPoint(x_m=x_m, y_m=y_m)))

        for source_id, target_id, attributes in graph.edges(data=True):
            _require_non_negative_finite_number(
                attributes.get(EDGE_LENGTH_ATTRIBUTE),
                field_name=(
                    f"edge {source_id!r}->{target_id!r} {EDGE_LENGTH_ATTRIBUTE}"
                ),
            )

        positions.sort(key=lambda item: item[0].node_id)
        return tuple(positions)

    def _require_known_node(self, node: NetworkNodeRef, *, field_name: str) -> str:
        if not isinstance(node, NetworkNodeRef):
            raise NetworkXBackendError(f"{field_name} must be a NetworkNodeRef")
        if node.node_id not in self._graph:
            raise NetworkXBackendError(
                f"{field_name} node {node.node_id!r} is not part of snapshot "
                f"{self._snapshot.snapshot_id!r}"
            )
        return node.node_id


def _build_heuristic_scale(
    graph: nx.Graph,
    positions: dict[str, NetworkPoint],
) -> float:
    scale = 1.0
    for source_id, target_id, attributes in graph.edges(data=True):
        source = positions[source_id]
        target = positions[target_id]
        straight_distance = hypot(source.x_m - target.x_m, source.y_m - target.y_m)
        if straight_distance <= 0.0:
            continue
        length_m = float(attributes[EDGE_LENGTH_ATTRIBUTE])
        scale = min(scale, length_m / straight_distance)
        if scale <= 0.0:
            return 0.0
    return max(0.0, min(1.0, scale))


def _require_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise NetworkXBackendError(f"{field_name} must be a finite number")
    return float(value)


def _require_non_negative_finite_number(value: object, *, field_name: str) -> float:
    number = _require_finite_number(value, field_name=field_name)
    if number < 0:
        raise NetworkXBackendError(f"{field_name} must be non-negative")
    return number


def _require_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise NetworkXBackendError(f"{field_name} must be a positive integer")
