from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.urban_generator.roads.road_graph import RoadGraph

DEFAULT_MAX_DEAD_END_RATIO = 0.35
DEFAULT_MAX_VALIDATION_EDGES = 1_000_000
DEFAULT_MAX_FORBIDDEN_GEOMETRIES = 10_000


class RoadValidationError(ValueError):
    """Raised when S06-T13 validation inputs are outside the bounded contract."""


class RoadValidationIssueCode(StrEnum):
    DISCONNECTED_COMPONENTS = "DISCONNECTED_COMPONENTS"
    DEAD_END_RATIO = "DEAD_END_RATIO"
    FORBIDDEN_CROSSING = "FORBIDDEN_CROSSING"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"


@dataclass(frozen=True, slots=True)
class RoadValidationPolicy:
    max_component_count: int = 1
    max_dead_end_ratio: float = DEFAULT_MAX_DEAD_END_RATIO
    max_forbidden_crossings: int = 0
    max_edges: int = DEFAULT_MAX_VALIDATION_EDGES
    max_forbidden_geometries: int = DEFAULT_MAX_FORBIDDEN_GEOMETRIES

    def __post_init__(self) -> None:
        if self.max_component_count <= 0 or self.max_edges <= 0:
            raise RoadValidationError("component and edge limits must be positive")
        if not 0.0 <= self.max_dead_end_ratio <= 1.0:
            raise RoadValidationError("max_dead_end_ratio must be in [0, 1]")
        if self.max_forbidden_crossings < 0 or self.max_forbidden_geometries < 0:
            raise RoadValidationError("forbidden limits must be non-negative")


@dataclass(frozen=True, slots=True)
class RoadValidationIssue:
    code: RoadValidationIssueCode
    message: str
    edge_id: str | None = None


@dataclass(frozen=True, slots=True)
class RoadValidationDiagnostics:
    node_count: int
    edge_count: int
    component_count: int
    dead_end_node_count: int
    dead_end_ratio: float
    invalid_geometry_count: int
    forbidden_crossing_count: int
    generated_edge_count: int


@dataclass(frozen=True, slots=True)
class RoadValidationResult:
    passed: bool
    issues: tuple[RoadValidationIssue, ...]
    diagnostics: RoadValidationDiagnostics
    validator_name: str = "road-network-validation"
    validator_version: str = "1"


class RoadNetworkValidator:
    """Validate topology plus generated-road geometry without mutating the graph."""

    def __init__(self, *, policy: RoadValidationPolicy | None = None) -> None:
        self.policy = policy or RoadValidationPolicy()

    def validate(
        self,
        *,
        graph: RoadGraph,
        forbidden_geometries: tuple[BaseGeometry, ...] = (),
    ) -> RoadValidationResult:
        if not isinstance(graph, RoadGraph):
            raise RoadValidationError("graph must be a RoadGraph")
        if len(graph.edges) > self.policy.max_edges:
            raise RoadValidationError("road validation edge limit exceeded")
        if not isinstance(forbidden_geometries, tuple):
            raise RoadValidationError("forbidden_geometries must be an immutable tuple")
        if len(forbidden_geometries) > self.policy.max_forbidden_geometries:
            raise RoadValidationError("forbidden geometry limit exceeded")
        for geometry in forbidden_geometries:
            if not isinstance(geometry, BaseGeometry) or geometry.is_empty or not geometry.is_valid:
                raise RoadValidationError("forbidden geometries must be valid and non-empty")

        degrees = {node.node.node_id: 0 for node in graph.nodes}
        invalid_ids: list[str] = []
        generated_edges = []
        for edge in graph.edges:
            if edge.source.node_id not in degrees or edge.target.node_id not in degrees:
                raise RoadValidationError("edge references a node missing from graph.nodes")
            degrees[edge.source.node_id] += 1
            degrees[edge.target.node_id] += 1
            geometry = edge.geometry
            if (
                geometry.is_empty
                or not geometry.is_valid
                or geometry.has_z
                or not isfinite(float(geometry.length))
                or geometry.length <= 0.0
            ):
                invalid_ids.append(edge.edge_id)
            if not edge.is_source:
                generated_edges.append(edge)

        dead_end_count = sum(degree == 1 for degree in degrees.values())
        dead_end_ratio = dead_end_count / len(graph.nodes) if graph.nodes else 0.0
        forbidden_ids: list[str] = []
        if forbidden_geometries:
            forbidden_union = unary_union(forbidden_geometries)
            forbidden_ids = sorted(
                edge.edge_id
                for edge in generated_edges
                if edge.edge_id not in invalid_ids and edge.geometry.intersects(forbidden_union)
            )

        diagnostics = RoadValidationDiagnostics(
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            component_count=graph.diagnostics.component_count,
            dead_end_node_count=dead_end_count,
            dead_end_ratio=dead_end_ratio,
            invalid_geometry_count=len(invalid_ids),
            forbidden_crossing_count=len(forbidden_ids),
            generated_edge_count=len(generated_edges),
        )
        issues: list[RoadValidationIssue] = []
        if diagnostics.component_count > self.policy.max_component_count:
            issues.append(RoadValidationIssue(
                RoadValidationIssueCode.DISCONNECTED_COMPONENTS,
                "road graph exceeds configured connected-component limit",
            ))
        if diagnostics.dead_end_ratio > self.policy.max_dead_end_ratio:
            issues.append(RoadValidationIssue(
                RoadValidationIssueCode.DEAD_END_RATIO,
                "road graph exceeds configured dead-end ratio",
            ))
        issues.extend(
            RoadValidationIssue(
                RoadValidationIssueCode.INVALID_GEOMETRY,
                "road edge geometry is invalid",
                edge_id,
            )
            for edge_id in sorted(invalid_ids)
        )
        if len(forbidden_ids) > self.policy.max_forbidden_crossings:
            issues.extend(
                RoadValidationIssue(
                    RoadValidationIssueCode.FORBIDDEN_CROSSING,
                    "generated road intersects forbidden geometry",
                    edge_id,
                )
                for edge_id in forbidden_ids
            )
        return RoadValidationResult(
            passed=not issues,
            issues=tuple(issues),
            diagnostics=diagnostics,
        )
