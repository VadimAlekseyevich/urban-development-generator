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

DEFAULT_ZONING_LAYER_LIMIT = 1000
MAX_ZONING_LAYER_LIMIT = 5000


class ZoningLayerQueryError(ValueError):
    """Raised when a zoning map query violates its public contract."""


class ZoningProjectNotFoundError(LookupError):
    """Raised when a zoning query references an unknown project."""


class ZoningRunNotFoundError(LookupError):
    """Raised when a run does not belong to the requested project."""


@dataclass(frozen=True, slots=True)
class ZoningRunSummary:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    zone_count: int
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class ZoningRunContext:
    project_id: uuid.UUID
    run_id: uuid.UUID
    working_srid: int
    status: str


@dataclass(frozen=True, slots=True)
class GeneratedZoneFeature:
    id: uuid.UUID
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class GeneratedZoneQueryResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    truncated: bool
    features: tuple[GeneratedZoneFeature, ...]
    type: Literal["FeatureCollection"] = field(init=False, default="FeatureCollection")


class ZoningLayerQueryRepository(Protocol):
    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        ...

    def list_runs(self, *, project_id: uuid.UUID) -> list[ZoningRunSummary]:
        ...

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> ZoningRunContext | None:
        ...

    def list_generated_zones(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[GeneratedZoneFeature]:
        ...


class ZoningLayerQueryService:
    """Application boundary for run selection and generated-zone viewport reads."""

    def __init__(self, repository: ZoningLayerQueryRepository) -> None:
        self._repository = repository

    def list_runs(self, *, project_id: uuid.UUID) -> tuple[ZoningRunSummary, ...]:
        if not self._repository.project_exists(project_id=project_id):
            raise ZoningProjectNotFoundError(project_id)
        return tuple(self._repository.list_runs(project_id=project_id))

    def get_generated_zones(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_ZONING_LAYER_LIMIT,
    ) -> GeneratedZoneQueryResult:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ZoningLayerQueryError("limit must be an integer")
        if limit < 1 or limit > MAX_ZONING_LAYER_LIMIT:
            raise ZoningLayerQueryError(
                f"limit must be between 1 and {MAX_ZONING_LAYER_LIMIT}"
            )
        try:
            bbox = SourceLayerBbox.parse(bbox_text)
        except SourceLayerQueryError as exc:
            raise ZoningLayerQueryError(str(exc)) from exc

        context = self._repository.get_run_context(
            project_id=project_id,
            run_id=run_id,
        )
        if context is None:
            raise ZoningRunNotFoundError(run_id)

        features = self._repository.list_generated_zones(
            run_id=run_id,
            working_srid=context.working_srid,
            bbox=bbox,
            limit=limit + 1,
        )
        truncated = len(features) > limit
        return GeneratedZoneQueryResult(
            project_id=project_id,
            run_id=run_id,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=context.working_srid,
            limit=limit,
            truncated=truncated,
            features=tuple(features[:limit]),
        )
