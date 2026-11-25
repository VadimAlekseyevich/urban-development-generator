from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from backend.app.application.source_layers import (
    GEOJSON_CRS,
    SourceLayerBbox,
    SourceLayerQueryError,
)

DEFAULT_ROAD_LAYER_LIMIT = 1000
MAX_ROAD_LAYER_LIMIT = 5000


class RoadLayerQueryError(ValueError):
    """Raised when a generated-road viewport query violates the public contract."""


class RoadLayerProjectNotFoundError(LookupError):
    """Raised when road runs are requested for an unknown project."""

    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        super().__init__(f"project {project_id} was not found")


class RoadLayerRunNotFoundError(LookupError):
    """Raised when a generation run does not belong to the requested project."""

    def __init__(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.run_id = run_id
        super().__init__(f"road run {run_id} was not found in project {project_id}")


@dataclass(frozen=True, slots=True)
class RoadRunSummary:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    generated_road_count: int
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class RoadRunContext:
    project_id: uuid.UUID
    run_id: uuid.UUID
    working_srid: int
    status: str


@dataclass(frozen=True, slots=True)
class GeneratedRoadFeature:
    id: uuid.UUID
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class GeneratedRoadQueryResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    truncated: bool
    features: tuple[GeneratedRoadFeature, ...]
    type: Literal["FeatureCollection"] = field(init=False, default="FeatureCollection")


@dataclass(frozen=True, slots=True)
class RoadDiagnosticEdge:
    road_id: str
    source_node_id: str | None
    target_node_id: str | None
    length_m: float
    road_class: str
    origin: str


@dataclass(frozen=True, slots=True)
class RoadGraphDiagnostics:
    run_id: uuid.UUID
    edge_count: int
    road_count: int
    node_count: int
    component_count: int
    dead_end_node_count: int
    dead_end_ratio: float
    total_length_m: float
    class_counts: dict[str, int]
    origin_counts: dict[str, int]


class RoadLayerQueryRepository(Protocol):
    """Read-only persistence port for generated-road map reads and diagnostics."""

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        ...

    def list_runs(self, *, project_id: uuid.UUID) -> list[RoadRunSummary]:
        ...

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> RoadRunContext | None:
        ...

    def list_generated_roads(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[GeneratedRoadFeature]:
        ...

    def list_diagnostic_edges(self, *, run_id: uuid.UUID) -> list[RoadDiagnosticEdge]:
        ...


def _graph_diagnostics(
    *,
    run_id: uuid.UUID,
    edges: list[RoadDiagnosticEdge],
) -> RoadGraphDiagnostics:
    parent: dict[str, str] = {}
    degree: Counter[str] = Counter()

    def add_node(node_id: str) -> None:
        parent.setdefault(node_id, node_id)

    def find(node_id: str) -> str:
        root = node_id
        while parent[root] != root:
            root = parent[root]
        while parent[node_id] != node_id:
            next_node = parent[node_id]
            parent[node_id] = root
            node_id = next_node
        return root

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for edge in edges:
        source = edge.source_node_id
        target = edge.target_node_id
        if source:
            add_node(source)
            degree[source] += 1
        if target:
            add_node(target)
            degree[target] += 1
        if source and target:
            union(source, target)

    node_count = len(parent)
    component_count = len({find(node_id) for node_id in parent})
    dead_end_node_count = sum(1 for node_id in parent if degree[node_id] == 1)
    dead_end_ratio = dead_end_node_count / node_count if node_count else 0.0
    class_counts = dict(sorted(Counter(edge.road_class for edge in edges).items()))
    origin_counts = dict(sorted(Counter(edge.origin for edge in edges).items()))
    road_ids = {edge.road_id for edge in edges if edge.road_id}

    return RoadGraphDiagnostics(
        run_id=run_id,
        edge_count=len(edges),
        road_count=len(road_ids),
        node_count=node_count,
        component_count=component_count,
        dead_end_node_count=dead_end_node_count,
        dead_end_ratio=dead_end_ratio,
        total_length_m=sum(max(0.0, edge.length_m) for edge in edges),
        class_counts=class_counts,
        origin_counts=origin_counts,
    )


class RoadLayerQueryService:
    """Validate road UI requests and keep HTTP independent from spatial SQL."""

    def __init__(self, repository: RoadLayerQueryRepository) -> None:
        self._repository = repository

    def list_runs(self, *, project_id: uuid.UUID) -> tuple[RoadRunSummary, ...]:
        if not self._repository.project_exists(project_id=project_id):
            raise RoadLayerProjectNotFoundError(project_id)
        return tuple(self._repository.list_runs(project_id=project_id))

    def get_generated_roads(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_ROAD_LAYER_LIMIT,
    ) -> GeneratedRoadQueryResult:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise RoadLayerQueryError("limit must be an integer")
        if limit < 1 or limit > MAX_ROAD_LAYER_LIMIT:
            raise RoadLayerQueryError(
                f"limit must be between 1 and {MAX_ROAD_LAYER_LIMIT}"
            )
        try:
            bbox = SourceLayerBbox.parse(bbox_text)
        except SourceLayerQueryError as exc:
            raise RoadLayerQueryError(str(exc)) from exc

        context = self._require_run(project_id=project_id, run_id=run_id)
        features = self._repository.list_generated_roads(
            run_id=run_id,
            working_srid=context.working_srid,
            bbox=bbox,
            limit=limit + 1,
        )
        truncated = len(features) > limit
        return GeneratedRoadQueryResult(
            project_id=project_id,
            run_id=run_id,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=context.working_srid,
            limit=limit,
            truncated=truncated,
            features=tuple(features[:limit]),
        )

    def get_diagnostics(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> RoadGraphDiagnostics:
        self._require_run(project_id=project_id, run_id=run_id)
        edges = self._repository.list_diagnostic_edges(run_id=run_id)
        return _graph_diagnostics(run_id=run_id, edges=edges)

    def _require_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> RoadRunContext:
        context = self._repository.get_run_context(
            project_id=project_id,
            run_id=run_id,
        )
        if context is None:
            raise RoadLayerRunNotFoundError(project_id, run_id)
        return context
