from __future__ import annotations

from math import isfinite

import networkx as nx

from core.urban_generator.domain import (
    NetworkBackend,
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


class NetworkXBackendError(NetworkContractError):
    """Raised when a NetworkX graph cannot satisfy the network domain contract."""


class NetworkXBackend(NetworkBackend):
    """NetworkX adapter for the backend-independent routing domain port.

    The adapter owns an immutable copy of the supplied graph. Node identifiers stay
    backend-independent strings; node coordinates and edge costs are expressed in metres
    in the supplied working CRS. Node snapping delegates to the reusable STRtree-backed
    spatial index instead of scanning all graph nodes.
    """

    def __init__(
        self,
        graph: nx.Graph,
        *,
        snapshot_id: str,
        working_crs: WorkingCRS,
        max_snap_targets: int = DEFAULT_MAX_SNAP_TARGETS,
    ) -> None:
        if not isinstance(graph, nx.Graph):
            raise NetworkXBackendError("graph must be a NetworkX graph")
        if not isinstance(working_crs, WorkingCRS):
            raise NetworkXBackendError("working_crs must be a validated WorkingCRS")

        graph_copy = graph.copy(as_view=False)
        positions = self._validate_graph(graph_copy)
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
    ) -> NetworkXBackend:
        """Adapt a backend-independent S06-T05 graph without collapsing parallel edges."""

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
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        source_id = self._require_known_node(source, field_name="source")
        target_id = self._require_known_node(target, field_name="target")
        limit = require_max_distance_m(max_distance_m)

        try:
            distance_m, path = nx.single_source_dijkstra(
                self._graph,
                source_id,
                target_id,
                cutoff=limit,
                weight=EDGE_LENGTH_ATTRIBUTE,
            )
        except nx.NetworkXNoPath:
            return None

        return NetworkPath(
            nodes=tuple(NetworkNodeRef(node_id=node_id) for node_id in path),
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
        source_ids = tuple(
            self._require_known_node(source, field_name="source") for source in sources
        )
        target_ids = tuple(
            self._require_known_node(target, field_name="target") for target in targets
        )
        limit = require_max_distance_m(max_distance_m)

        distances, paths = nx.multi_source_dijkstra(
            self._graph,
            source_ids,
            cutoff=limit,
            weight=EDGE_LENGTH_ATTRIBUTE,
        )

        results: list[NetworkDistanceResult] = []
        for target, target_id in zip(targets, target_ids, strict=True):
            if target_id not in distances:
                continue
            path = paths[target_id]
            results.append(
                NetworkDistanceResult(
                    source=NetworkNodeRef(node_id=path[0]),
                    target=target,
                    distance_m=float(distances[target_id]),
                )
            )
        return tuple(results)

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


def _require_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise NetworkXBackendError(f"{field_name} must be a finite number")
    return float(value)


def _require_non_negative_finite_number(value: object, *, field_name: str) -> float:
    number = _require_finite_number(value, field_name=field_name)
    if number < 0:
        raise NetworkXBackendError(f"{field_name} must be non-negative")
    return number
