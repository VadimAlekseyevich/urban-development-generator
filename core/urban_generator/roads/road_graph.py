from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from shapely.geometry import LineString

from core.urban_generator.domain.crs import WorkingCRS, require_working_crs
from core.urban_generator.domain.network import NetworkNodeRef, NetworkPoint
from core.urban_generator.domain.semantics import WorldStateContract
from core.urban_generator.roads.semantic_noding import NodedRoad

DEFAULT_MAX_GRAPH_NODES = 1_000_000
DEFAULT_MAX_GRAPH_EDGES = 1_000_000


class RoadGraphBuildError(ValueError):
    """Raised when noded roads cannot satisfy the bounded graph-build contract."""


@dataclass(frozen=True, slots=True)
class RoadGraphInput:
    """One noded road paired with immutable source/generated ownership semantics."""

    road: NodedRoad
    state: WorldStateContract

    def __post_init__(self) -> None:
        if not isinstance(self.road, NodedRoad):
            raise RoadGraphBuildError("road must be a NodedRoad")
        if not isinstance(self.state, WorldStateContract):
            raise RoadGraphBuildError("state must be a WorldStateContract")


@dataclass(frozen=True, slots=True)
class RoadGraphNode:
    """Backend-independent graph node at one exact noded endpoint."""

    node: NetworkNodeRef
    point: NetworkPoint
    is_source: bool
    is_fixed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.node, NetworkNodeRef):
            raise RoadGraphBuildError("node must be a NetworkNodeRef")
        if not isinstance(self.point, NetworkPoint):
            raise RoadGraphBuildError("point must be a NetworkPoint")
        if not isinstance(self.is_source, bool):
            raise RoadGraphBuildError("is_source must be a bool")
        if not isinstance(self.is_fixed, bool):
            raise RoadGraphBuildError("is_fixed must be a bool")
        if self.is_fixed and not self.is_source:
            raise RoadGraphBuildError("fixed graph nodes must participate in source state")


@dataclass(frozen=True, slots=True)
class RoadGraphEdge:
    """One directed-by-geometry road part between two graph nodes.

    The graph itself is connectivity-neutral at this stage. ``source`` and ``target`` only
    preserve the original LineString coordinate order so later traversal semantics can interpret
    one-way metadata without guessing or reversing geometry.
    """

    edge_id: str
    source: NetworkNodeRef
    target: NetworkNodeRef
    road_id: str
    part_index: int
    geometry: LineString
    length_m: float
    state: WorldStateContract

    def __post_init__(self) -> None:
        _require_non_empty_id(self.edge_id, field_name="edge_id")
        if not isinstance(self.source, NetworkNodeRef):
            raise RoadGraphBuildError("source must be a NetworkNodeRef")
        if not isinstance(self.target, NetworkNodeRef):
            raise RoadGraphBuildError("target must be a NetworkNodeRef")
        _require_non_empty_id(self.road_id, field_name="road_id")
        if isinstance(self.part_index, bool) or not isinstance(self.part_index, int):
            raise RoadGraphBuildError("part_index must be an integer")
        if self.part_index < 0:
            raise RoadGraphBuildError("part_index must be non-negative")
        _require_metric_line(self.geometry)
        if (
            isinstance(self.length_m, bool)
            or not isinstance(self.length_m, (int, float))
            or not isfinite(self.length_m)
            or self.length_m <= 0.0
        ):
            raise RoadGraphBuildError("length_m must be a positive finite number")
        if not isinstance(self.state, WorldStateContract):
            raise RoadGraphBuildError("state must be a WorldStateContract")

    @property
    def is_source(self) -> bool:
        return self.state.is_fixed_source

    @property
    def is_fixed(self) -> bool:
        return self.state.is_fixed_source


@dataclass(frozen=True, slots=True)
class RoadGraphComponentDiagnostics:
    """Connectivity summary for one undirected road component."""

    component_id: str
    node_count: int
    edge_count: int
    total_length_m: float
    source_edge_count: int
    fixed_edge_count: int


@dataclass(frozen=True, slots=True)
class RoadGraphDiagnostics:
    """Graph-build and connected-component diagnostics."""

    node_count: int
    edge_count: int
    total_length_m: float
    source_edge_count: int
    fixed_edge_count: int
    component_count: int
    components: tuple[RoadGraphComponentDiagnostics, ...]


@dataclass(frozen=True, slots=True)
class RoadGraph:
    """Backend-independent noded road graph ready for routing adapters."""

    working_crs: WorkingCRS
    nodes: tuple[RoadGraphNode, ...]
    edges: tuple[RoadGraphEdge, ...]
    diagnostics: RoadGraphDiagnostics


@dataclass(frozen=True, slots=True)
class _EdgeDraft:
    road_id: str
    part_index: int
    geometry: LineString
    length_m: float
    state: WorldStateContract
    source_key: tuple[float, float]
    target_key: tuple[float, float]


class RoadGraphBuilder:
    """Build a deterministic graph from already-snapped, semantically noded roads.

    Exact endpoint coordinates define graph nodes. Snapping belongs to S06-T03 and intersection
    semantics belong to S06-T04; this builder intentionally does neither. Input roads are sorted
    by stable ``road_id`` and nodes by coordinate so identifiers are deterministic independent of
    caller ordering. Connectivity diagnostics use a union-find pass and therefore remain O(V+E).
    """

    def __init__(
        self,
        *,
        working_srid: int,
        max_nodes: int = DEFAULT_MAX_GRAPH_NODES,
        max_edges: int = DEFAULT_MAX_GRAPH_EDGES,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        _require_positive_int(max_nodes, field_name="max_nodes")
        _require_positive_int(max_edges, field_name="max_edges")
        self.max_nodes = max_nodes
        self.max_edges = max_edges

    def build(self, roads: tuple[RoadGraphInput, ...]) -> RoadGraph:
        if not isinstance(roads, tuple):
            raise RoadGraphBuildError("roads must be an immutable tuple")

        ordered_roads = sorted(roads, key=lambda item: item.road.road_id if isinstance(item, RoadGraphInput) else "")
        seen_road_ids: set[str] = set()
        node_flags: dict[tuple[float, float], tuple[bool, bool]] = {}
        drafts: list[_EdgeDraft] = []

        for index, item in enumerate(ordered_roads):
            if not isinstance(item, RoadGraphInput):
                raise RoadGraphBuildError(f"roads[{index}] must be a RoadGraphInput")
            road_id = item.road.road_id
            if road_id in seen_road_ids:
                raise RoadGraphBuildError(f"duplicate road_id: {road_id!r}")
            seen_road_ids.add(road_id)

            for part_index, geometry in enumerate(item.road.parts):
                _require_metric_line(geometry)
                source_key = _endpoint_key(geometry, at_start=True)
                target_key = _endpoint_key(geometry, at_start=False)
                length_m = float(geometry.length)
                if not isfinite(length_m) or length_m <= 0.0:
                    raise RoadGraphBuildError("road part length must be a positive finite number")

                drafts.append(
                    _EdgeDraft(
                        road_id=road_id,
                        part_index=part_index,
                        geometry=geometry,
                        length_m=length_m,
                        state=item.state,
                        source_key=source_key,
                        target_key=target_key,
                    )
                )
                if len(drafts) > self.max_edges:
                    raise RoadGraphBuildError(
                        f"graph edge limit exceeded: {len(drafts)} > {self.max_edges}"
                    )

                is_source = item.state.is_fixed_source
                is_fixed = item.state.is_fixed_source
                _merge_node_flags(node_flags, source_key, is_source=is_source, is_fixed=is_fixed)
                _merge_node_flags(node_flags, target_key, is_source=is_source, is_fixed=is_fixed)

        ordered_keys = sorted(node_flags)
        if len(ordered_keys) > self.max_nodes:
            raise RoadGraphBuildError(
                f"graph node limit exceeded: {len(ordered_keys)} > {self.max_nodes}"
            )

        node_refs = {
            key: NetworkNodeRef(node_id=f"node:{index:08d}")
            for index, key in enumerate(ordered_keys)
        }
        nodes = tuple(
            RoadGraphNode(
                node=node_refs[key],
                point=NetworkPoint(x_m=key[0], y_m=key[1]),
                is_source=node_flags[key][0],
                is_fixed=node_flags[key][1],
            )
            for key in ordered_keys
        )
        edges = tuple(
            RoadGraphEdge(
                edge_id=f"edge:{index:08d}",
                source=node_refs[draft.source_key],
                target=node_refs[draft.target_key],
                road_id=draft.road_id,
                part_index=draft.part_index,
                geometry=draft.geometry,
                length_m=draft.length_m,
                state=draft.state,
            )
            for index, draft in enumerate(drafts)
        )
        diagnostics = _build_diagnostics(nodes, edges)
        return RoadGraph(
            working_crs=self.working_crs,
            nodes=nodes,
            edges=edges,
            diagnostics=diagnostics,
        )


def _build_diagnostics(
    nodes: tuple[RoadGraphNode, ...],
    edges: tuple[RoadGraphEdge, ...],
) -> RoadGraphDiagnostics:
    if not nodes:
        return RoadGraphDiagnostics(
            node_count=0,
            edge_count=0,
            total_length_m=0.0,
            source_edge_count=0,
            fixed_edge_count=0,
            component_count=0,
            components=(),
        )

    node_indexes = {node.node.node_id: index for index, node in enumerate(nodes)}
    parent = list(range(len(nodes)))
    size = [1] * len(nodes)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if size[left_root] < size[right_root]:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        size[left_root] += size[right_root]

    for edge in edges:
        union(node_indexes[edge.source.node_id], node_indexes[edge.target.node_id])

    component_nodes: dict[int, list[str]] = {}
    for node in nodes:
        index = node_indexes[node.node.node_id]
        component_nodes.setdefault(find(index), []).append(node.node.node_id)

    edge_counts: dict[int, int] = {}
    lengths: dict[int, float] = {}
    source_counts: dict[int, int] = {}
    fixed_counts: dict[int, int] = {}
    for edge in edges:
        root = find(node_indexes[edge.source.node_id])
        edge_counts[root] = edge_counts.get(root, 0) + 1
        lengths[root] = lengths.get(root, 0.0) + edge.length_m
        source_counts[root] = source_counts.get(root, 0) + int(edge.is_source)
        fixed_counts[root] = fixed_counts.get(root, 0) + int(edge.is_fixed)

    components = tuple(
        sorted(
            (
                RoadGraphComponentDiagnostics(
                    component_id=min(node_ids),
                    node_count=len(node_ids),
                    edge_count=edge_counts.get(root, 0),
                    total_length_m=lengths.get(root, 0.0),
                    source_edge_count=source_counts.get(root, 0),
                    fixed_edge_count=fixed_counts.get(root, 0),
                )
                for root, node_ids in component_nodes.items()
            ),
            key=lambda item: (-item.node_count, -item.edge_count, item.component_id),
        )
    )
    total_length_m = sum(edge.length_m for edge in edges)
    source_edge_count = sum(int(edge.is_source) for edge in edges)
    fixed_edge_count = sum(int(edge.is_fixed) for edge in edges)
    return RoadGraphDiagnostics(
        node_count=len(nodes),
        edge_count=len(edges),
        total_length_m=total_length_m,
        source_edge_count=source_edge_count,
        fixed_edge_count=fixed_edge_count,
        component_count=len(components),
        components=components,
    )


def _endpoint_key(geometry: LineString, *, at_start: bool) -> tuple[float, float]:
    coordinate = geometry.coords[0] if at_start else geometry.coords[-1]
    return (float(coordinate[0]), float(coordinate[1]))


def _merge_node_flags(
    flags: dict[tuple[float, float], tuple[bool, bool]],
    key: tuple[float, float],
    *,
    is_source: bool,
    is_fixed: bool,
) -> None:
    current = flags.get(key, (False, False))
    flags[key] = (current[0] or is_source, current[1] or is_fixed)


def _require_metric_line(geometry: LineString) -> None:
    if not isinstance(geometry, LineString):
        raise RoadGraphBuildError("road parts must be LineStrings")
    if geometry.is_empty or not geometry.is_valid or geometry.length <= 0.0:
        raise RoadGraphBuildError("road parts must be valid positive-length LineStrings")
    if geometry.has_z:
        raise RoadGraphBuildError("road parts must be 2D in the working CRS")
    for coordinate in geometry.coords:
        if len(coordinate) < 2 or not all(isfinite(float(value)) for value in coordinate[:2]):
            raise RoadGraphBuildError("road part coordinates must be finite")


def _require_non_empty_id(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RoadGraphBuildError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise RoadGraphBuildError(f"{field_name} must not contain line breaks")


def _require_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RoadGraphBuildError(f"{field_name} must be a positive integer")
