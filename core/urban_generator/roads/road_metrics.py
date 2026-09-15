from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite

from core.urban_generator.roads.road_graph import RoadGraph

DEFAULT_MAX_METRIC_NODES = 1_000_000
DEFAULT_MAX_METRIC_EDGES = 1_000_000


class RoadMetricsError(ValueError):
    """Raised when S06-T14 metric inputs violate the bounded metric contract."""


@dataclass(frozen=True, slots=True)
class RoadMetricsPolicy:
    """Explicit work bounds for deterministic O(V+E) road metrics."""

    max_nodes: int = DEFAULT_MAX_METRIC_NODES
    max_edges: int = DEFAULT_MAX_METRIC_EDGES
    intersection_min_degree: int = 3

    def __post_init__(self) -> None:
        for field_name in ("max_nodes", "max_edges", "intersection_min_degree"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RoadMetricsError(f"{field_name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RoadMetrics:
    """Raw explainable S06-T14 metrics for one metric-CRS road graph."""

    analysis_area_m2: float
    node_count: int
    edge_count: int
    component_count: int
    total_length_m: float
    generated_length_m: float
    length_density_km_per_km2: float
    mean_degree: float
    max_degree: int
    intersection_count: int
    intersection_density_per_km2: float
    edge_weighted_circuity: float | None
    circuity_edge_count: int


class RoadMetricsCalculator:
    """Calculate bounded topology/density metrics without persistence or validation policy."""

    name = "road-metrics"
    version = "1"

    def __init__(self, *, policy: RoadMetricsPolicy | None = None) -> None:
        self.policy = policy or RoadMetricsPolicy()
        if not isinstance(self.policy, RoadMetricsPolicy):
            raise RoadMetricsError("policy must be a RoadMetricsPolicy")

    def calculate(self, *, graph: RoadGraph, analysis_area_m2: float) -> RoadMetrics:
        if not isinstance(graph, RoadGraph):
            raise RoadMetricsError("graph must be a RoadGraph")
        area = _positive_finite("analysis_area_m2", analysis_area_m2)
        if len(graph.nodes) > self.policy.max_nodes:
            raise RoadMetricsError("road metric node limit exceeded")
        if len(graph.edges) > self.policy.max_edges:
            raise RoadMetricsError("road metric edge limit exceeded")

        degrees = {node.node.node_id: 0 for node in graph.nodes}
        total_length_m = 0.0
        generated_length_m = 0.0
        chord_length_m = 0.0
        routed_length_m = 0.0
        circuity_edge_count = 0

        points = {
            node.node.node_id: (node.point.x_m, node.point.y_m)
            for node in graph.nodes
        }
        for edge in graph.edges:
            if edge.source.node_id not in degrees or edge.target.node_id not in degrees:
                raise RoadMetricsError("edge references a node missing from graph.nodes")
            degrees[edge.source.node_id] += 1
            degrees[edge.target.node_id] += 1
            total_length_m += edge.length_m
            if not edge.is_source:
                generated_length_m += edge.length_m

            sx, sy = points[edge.source.node_id]
            tx, ty = points[edge.target.node_id]
            chord = hypot(tx - sx, ty - sy)
            if chord > 0.0:
                chord_length_m += chord
                routed_length_m += edge.length_m
                circuity_edge_count += 1

        degree_values = tuple(degrees.values())
        intersection_count = sum(
            degree >= self.policy.intersection_min_degree for degree in degree_values
        )
        area_km2 = area / 1_000_000.0
        circuity = (
            routed_length_m / chord_length_m if chord_length_m > 0.0 else None
        )
        return RoadMetrics(
            analysis_area_m2=area,
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            component_count=graph.diagnostics.component_count,
            total_length_m=total_length_m,
            generated_length_m=generated_length_m,
            length_density_km_per_km2=(total_length_m / 1_000.0) / area_km2,
            mean_degree=(sum(degree_values) / len(degree_values) if degree_values else 0.0),
            max_degree=max(degree_values, default=0),
            intersection_count=intersection_count,
            intersection_density_per_km2=intersection_count / area_km2,
            edge_weighted_circuity=circuity,
            circuity_edge_count=circuity_edge_count,
        )


def _positive_finite(field_name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RoadMetricsError(f"{field_name} must be a positive finite number")
    result = float(value)
    if not isfinite(result) or result <= 0.0:
        raise RoadMetricsError(f"{field_name} must be a positive finite number")
    return result
