from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from backend.app.application.source_layers import (
    GEOJSON_CRS,
    SourceLayerBbox,
    SourceLayerQueryError,
)

DEFAULT_BUILDING_LAYER_LIMIT = 1500
MAX_BUILDING_LAYER_LIMIT = 5000


class BuildingLayerQueryError(ValueError):
    """Raised when a generated-building viewport request is invalid."""


class BuildingLayerProjectNotFoundError(LookupError):
    """Raised when building runs are requested for an unknown project."""

    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        super().__init__(f"project {project_id} was not found")


class BuildingLayerRunNotFoundError(LookupError):
    """Raised when a generation run does not belong to the requested project."""

    def __init__(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.run_id = run_id
        super().__init__(
            f"building run {run_id} was not found in project {project_id}"
        )


@dataclass(frozen=True, slots=True)
class BuildingRunSummary:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    generated_building_count: int
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class BuildingRunContext:
    project_id: uuid.UUID
    run_id: uuid.UUID
    working_srid: int
    status: str


@dataclass(frozen=True, slots=True)
class BuildingFeature:
    id: uuid.UUID
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class BuildingQueryResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    truncated: bool
    features: tuple[BuildingFeature, ...]
    type: Literal["FeatureCollection"] = field(
        init=False,
        default="FeatureCollection",
    )


class BuildingLayerQueryRepository(Protocol):
    """Read-only persistence port for generated-building map reads."""

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        ...

    def list_runs(self, *, project_id: uuid.UUID) -> list[BuildingRunSummary]:
        ...

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> BuildingRunContext | None:
        ...

    def list_buildings(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[BuildingFeature]:
        ...


class BuildingLayerQueryService:
    """Validate generated-building map reads and keep spatial SQL in an adapter."""

    def __init__(self, repository: BuildingLayerQueryRepository) -> None:
        self._repository = repository

    def list_runs(
        self,
        *,
        project_id: uuid.UUID,
    ) -> tuple[BuildingRunSummary, ...]:
        if not self._repository.project_exists(project_id=project_id):
            raise BuildingLayerProjectNotFoundError(project_id)
        return tuple(self._repository.list_runs(project_id=project_id))

    def get_buildings(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_BUILDING_LAYER_LIMIT,
    ) -> BuildingQueryResult:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise BuildingLayerQueryError("limit must be an integer")
        if limit < 1 or limit > MAX_BUILDING_LAYER_LIMIT:
            raise BuildingLayerQueryError(
                f"limit must be between 1 and {MAX_BUILDING_LAYER_LIMIT}"
            )
        try:
            bbox = SourceLayerBbox.parse(bbox_text)
        except SourceLayerQueryError as exc:
            raise BuildingLayerQueryError(str(exc)) from exc

        context = self._repository.get_run_context(
            project_id=project_id,
            run_id=run_id,
        )
        if context is None:
            raise BuildingLayerRunNotFoundError(project_id, run_id)

        features = self._repository.list_buildings(
            run_id=run_id,
            working_srid=context.working_srid,
            bbox=bbox,
            limit=limit + 1,
        )
        truncated = len(features) > limit
        return BuildingQueryResult(
            project_id=project_id,
            run_id=run_id,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=context.working_srid,
            limit=limit,
            truncated=truncated,
            features=tuple(features[:limit]),
        )
