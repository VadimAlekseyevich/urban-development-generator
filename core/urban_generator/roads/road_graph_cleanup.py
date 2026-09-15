from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from core.urban_generator.domain.crs import WorkingCRS
from core.urban_generator.roads.road_graph import (
    DEFAULT_MAX_GRAPH_EDGES,
    DEFAULT_MAX_GRAPH_NODES,
    RoadGraph,
    RoadGraphEdge,
    RoadGraphNode,
    build_road_graph_diagnostics,
)

DEFAULT_TINY_EDGE_THRESHOLD_M = 0.0
DEFAULT_DANGLING_EDGE_THRESHOLD_M = 0.0
DEFAULT_MAX_DANGLING_PRUNE_PASSES = 16


class RoadGraphCleanupError(ValueError):
    """Raised when a graph cannot satisfy the bounded cleanup contract."""


@dataclass(frozen=True, slots=True)
class RoadGraphCleanupPolicy:
    """Explicit metric thresholds and work bounds for graph cleanup.

    Zero metre thresholds intentionally disable destructive tiny/dangling cleanup by default.
    Callers must choose project-appropriate thresholds relative to their snapping/precision policy.
    Exact duplicate cleanup is always enabled.
    """

    tiny_edge_threshold_m: float = DEFAULT_TINY_EDGE_THRESHOLD_M
    dangling_edge_threshold_m: float = DEFAULT_DANGLING_EDGE_THRESHOLD_M
    max_prune_passes: int = DEFAULT_MAX_DANGLING_PRUNE_PASSES
    max_nodes: int = DEFAULT_MAX_GRAPH_NODES
    max_edges: int = DEFAULT_MAX_GRAPH_EDGES

    def __post_init__(self) -> None:
        _require_non_negative_finite_number(
            self.tiny_edge_threshold_m,
            field_name="tiny_edge_threshold_m",
        )
        _require_non_negative_finite_number(
            self.dangling_edge_threshold_m,
            field_name="dangling_edge_threshold_m",
        )
        _require_positive_int(self.max_prune_passes, field_name="max_prune_passes")
        _require_positive_int(self.max_nodes, field_name="max_nodes")
        _require_positive_int(self.max_edges, field_name="max_edges")


@dataclass(frozen=True, slots=True)
class RoadGraphCleanupDiagnostics:
    """Deterministic summary of cleanup decisions and remaining bounded artifacts."""

    input_node_count: int
    input_edge_count: int
    output_node_count: int
    output_edge_count: int
    duplicate_edges_removed: int
    tiny_edges_removed: int
    dangling_edges_removed: int
    orphan_nodes_removed: int
    fixed_tiny_edges_retained: int
    fixed_dangling_edges_retained: int
    generated_dangling_edges_remaining: int
    dangling_prune_passes: int
    prune_limit_reached: bool


@dataclass(frozen=True, slots=True)
class RoadGraphCleanupResult:
    """Cleaned backend-independent graph plus cleanup-specific diagnostics."""

    graph: RoadGraph
    diagnostics: RoadGraphCleanupDiagnostics


class RoadGraphCleaner:
    """Clean exact duplicates and bounded tiny/dangling graph artifacts.

    The cleaner is connectivity-neutral and does not perform snapping, semantic noding or routing.
    Duplicate detection is deliberately conservative: only identical 2D coordinate chains are
    equivalent (orientation may be reversed). Parallel edges with different geometry are retained.
    Fixed source edges may win duplicate groups but are never removed by tiny/dangling policies.
    Dangling pruning is O(max_prune_passes * E) with explicit node/edge caps.
    """

    def __init__(self, policy: RoadGraphCleanupPolicy | None = None) -> None:
        if policy is None:
            policy = RoadGraphCleanupPolicy()
        if not isinstance(policy, RoadGraphCleanupPolicy):
            raise RoadGraphCleanupError("policy must be a RoadGraphCleanupPolicy")
        self.policy = policy

    def cleanup(self, graph: RoadGraph) -> RoadGraphCleanupResult:
        node_by_id = self._validate_graph(graph)
        input_node_count = len(graph.nodes)
        input_edge_count = len(graph.edges)

        edges, duplicate_edges_removed = _remove_exact_duplicates(graph.edges)
        edges, tiny_edges_removed, fixed_tiny_edges_retained = self._remove_tiny_edges(edges)
        (
            edges,
            dangling_edges_removed,
            dangling_prune_passes,
        ) = self._remove_generated_dangling_edges(edges)

        degrees = _degree_counts(edges)
        fixed_dangling_edges_retained = sum(
            1
            for edge in edges
            if edge.is_fixed
            and _is_dangling(
                edge,
                degrees,
                threshold_m=self.policy.dangling_edge_threshold_m,
            )
        )
        generated_dangling_edges_remaining = sum(
            1
            for edge in edges
            if not edge.is_fixed
            and _is_dangling(
                edge,
                degrees,
                threshold_m=self.policy.dangling_edge_threshold_m,
            )
        )
        prune_limit_reached = generated_dangling_edges_remaining > 0

        nodes = _rebuild_nodes(node_by_id, edges)
        graph_diagnostics = build_road_graph_diagnostics(nodes, edges)
        cleaned_graph = RoadGraph(
            working_crs=graph.working_crs,
            nodes=nodes,
            edges=edges,
            diagnostics=graph_diagnostics,
        )
        diagnostics = RoadGraphCleanupDiagnostics(
            input_node_count=input_node_count,
            input_edge_count=input_edge_count,
            output_node_count=len(nodes),
            output_edge_count=len(edges),
            duplicate_edges_removed=duplicate_edges_removed,
            tiny_edges_removed=tiny_edges_removed,
            dangling_edges_removed=dangling_edges_removed,
            orphan_nodes_removed=input_node_count - len(nodes),
            fixed_tiny_edges_retained=fixed_tiny_edges_retained,
            fixed_dangling_edges_retained=fixed_dangling_edges_retained,
            generated_dangling_edges_remaining=generated_dangling_edges_remaining,
            dangling_prune_passes=dangling_prune_passes,
            prune_limit_reached=prune_limit_reached,
        )
        return RoadGraphCleanupResult(graph=cleaned_graph, diagnostics=diagnostics)

    def _validate_graph(self, graph: RoadGraph) -> dict[str, RoadGraphNode]:
        if not isinstance(graph, RoadGraph):
            raise RoadGraphCleanupError("graph must be a RoadGraph")
        if not isinstance(graph.working_crs, WorkingCRS):
            raise RoadGraphCleanupError("graph working_crs must be a validated WorkingCRS")
        if not isinstance(graph.nodes, tuple):
            raise RoadGraphCleanupError("graph nodes must be an immutable tuple")
        if not isinstance(graph.edges, tuple):
            raise RoadGraphCleanupError("graph edges must be an immutable tuple")
        if len(graph.nodes) > self.policy.max_nodes:
            raise RoadGraphCleanupError(
                f"cleanup node limit exceeded: {len(graph.nodes)} > {self.policy.max_nodes}"
            )
        if len(graph.edges) > self.policy.max_edges:
            raise RoadGraphCleanupError(
                f"cleanup edge limit exceeded: {len(graph.edges)} > {self.policy.max_edges}"
            )

        node_by_id: dict[str, RoadGraphNode] = {}
        point_owners: dict[tuple[float, float], str] = {}
        for index, node in enumerate(graph.nodes):
            if not isinstance(node, RoadGraphNode):
                raise RoadGraphCleanupError(f"graph.nodes[{index}] must be a RoadGraphNode")
            node_id = node.node.node_id
            if node_id in node_by_id:
                raise RoadGraphCleanupError(f"duplicate graph node id: {node_id!r}")
            point_key = (node.point.x_m, node.point.y_m)
            if point_key in point_owners:
                raise RoadGraphCleanupError(
                    "graph nodes must have unique exact coordinates; "
                    f"{node_id!r} duplicates {point_owners[point_key]!r}"
                )
            node_by_id[node_id] = node
            point_owners[point_key] = node_id

        seen_edge_ids: set[str] = set()
        for index, edge in enumerate(graph.edges):
            if not isinstance(edge, RoadGraphEdge):
                raise RoadGraphCleanupError(f"graph.edges[{index}] must be a RoadGraphEdge")
            if edge.edge_id in seen_edge_ids:
                raise RoadGraphCleanupError(f"duplicate graph edge id: {edge.edge_id!r}")
            seen_edge_ids.add(edge.edge_id)
            source = node_by_id.get(edge.source.node_id)
            target = node_by_id.get(edge.target.node_id)
            if source is None or target is None:
                raise RoadGraphCleanupError(
                    f"edge {edge.edge_id!r} references a node outside the graph"
                )
            start = edge.geometry.coords[0]
            end = edge.geometry.coords[-1]
            if (float(start[0]), float(start[1])) != (source.point.x_m, source.point.y_m):
                raise RoadGraphCleanupError(
                    f"edge {edge.edge_id!r} source does not match geometry start"
                )
            if (float(end[0]), float(end[1])) != (target.point.x_m, target.point.y_m):
                raise RoadGraphCleanupError(
                    f"edge {edge.edge_id!r} target does not match geometry end"
                )
        return node_by_id

    def _remove_tiny_edges(
        self,
        edges: tuple[RoadGraphEdge, ...],
    ) -> tuple[tuple[RoadGraphEdge, ...], int, int]:
        retained: list[RoadGraphEdge] = []
        removed = 0
        fixed_retained = 0
        threshold = self.policy.tiny_edge_threshold_m
        for edge in edges:
            if edge.length_m <= threshold:
                if edge.is_fixed:
                    fixed_retained += 1
                    retained.append(edge)
                else:
                    removed += 1
                continue
            retained.append(edge)
        return tuple(retained), removed, fixed_retained

    def _remove_generated_dangling_edges(
        self,
        edges: tuple[RoadGraphEdge, ...],
    ) -> tuple[tuple[RoadGraphEdge, ...], int, int]:
        remaining = edges
        removed = 0
        completed_passes = 0
        threshold = self.policy.dangling_edge_threshold_m

        for _ in range(self.policy.max_prune_passes):
            degrees = _degree_counts(remaining)
            removable_ids = {
                edge.edge_id
                for edge in remaining
                if not edge.is_fixed
                and _is_dangling(edge, degrees, threshold_m=threshold)
            }
            if not removable_ids:
                break
            remaining = tuple(
                edge for edge in remaining if edge.edge_id not in removable_ids
            )
            removed += len(removable_ids)
            completed_passes += 1

        return remaining, removed, completed_passes


def _remove_exact_duplicates(
    edges: tuple[RoadGraphEdge, ...],
) -> tuple[tuple[RoadGraphEdge, ...], int]:
    groups: dict[tuple[tuple[float, float], ...], list[RoadGraphEdge]] = {}
    for edge in edges:
        groups.setdefault(_canonical_geometry_key(edge), []).append(edge)

    retained: list[RoadGraphEdge] = []
    removed = 0
    for group in groups.values():
        representative = min(group, key=_duplicate_preference)
        retained.append(representative)
        removed += len(group) - 1
    retained.sort(key=lambda edge: edge.edge_id)
    return tuple(retained), removed


def _canonical_geometry_key(edge: RoadGraphEdge) -> tuple[tuple[float, float], ...]:
    coordinates = tuple(
        (float(coordinate[0]), float(coordinate[1])) for coordinate in edge.geometry.coords
    )
    reversed_coordinates = tuple(reversed(coordinates))
    return min(coordinates, reversed_coordinates)


def _duplicate_preference(edge: RoadGraphEdge) -> tuple[int, str]:
    return (0 if edge.is_fixed else 1, edge.edge_id)


def _degree_counts(edges: tuple[RoadGraphEdge, ...]) -> dict[str, int]:
    degrees: dict[str, int] = {}
    for edge in edges:
        degrees[edge.source.node_id] = degrees.get(edge.source.node_id, 0) + 1
        degrees[edge.target.node_id] = degrees.get(edge.target.node_id, 0) + 1
    return degrees


def _is_dangling(
    edge: RoadGraphEdge,
    degrees: dict[str, int],
    *,
    threshold_m: float,
) -> bool:
    if edge.length_m > threshold_m:
        return False
    return degrees.get(edge.source.node_id, 0) == 1 or degrees.get(edge.target.node_id, 0) == 1


def _rebuild_nodes(
    node_by_id: dict[str, RoadGraphNode],
    edges: tuple[RoadGraphEdge, ...],
) -> tuple[RoadGraphNode, ...]:
    flags: dict[str, tuple[bool, bool]] = {}
    for edge in edges:
        for node_id in (edge.source.node_id, edge.target.node_id):
            current = flags.get(node_id, (False, False))
            flags[node_id] = (
                current[0] or edge.is_source,
                current[1] or edge.is_fixed,
            )

    return tuple(
        RoadGraphNode(
            node=node_by_id[node_id].node,
            point=node_by_id[node_id].point,
            is_source=is_source,
            is_fixed=is_fixed,
        )
        for node_id, (is_source, is_fixed) in sorted(flags.items())
    )


def _require_non_negative_finite_number(value: float, *, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0.0
    ):
        raise RoadGraphCleanupError(f"{field_name} must be a non-negative finite number")


def _require_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RoadGraphCleanupError(f"{field_name} must be a positive integer")
