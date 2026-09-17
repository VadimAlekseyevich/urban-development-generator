from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from backend.app.application.source_layers import GEOJSON_CRS, SourceLayerBbox, SourceLayerQueryError

DEFAULT_BLOCK_PARCEL_LAYER_LIMIT = 1000
MAX_BLOCK_PARCEL_LAYER_LIMIT = 5000


class BlockParcelLayerQueryError(ValueError):
    """Raised when a block/parcel viewport request violates the public contract."""


class BlockParcelLayerProjectNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        super().__init__(f"project {project_id} was not found")


class BlockParcelLayerRunNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.run_id = run_id
        super().__init__(f"block/parcel run {run_id} was not found in project {project_id}")


@dataclass(frozen=True, slots=True)
class BlockParcelRunSummary:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    generated_block_count: int
    generated_parcel_count: int
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class BlockParcelRunContext:
    project_id: uuid.UUID
    run_id: uuid.UUID
    working_srid: int
    status: str


@dataclass(frozen=True, slots=True)
class BlockParcelFeature:
    id: uuid.UUID
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class BlockParcelQueryResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    truncated: bool
    features: tuple[BlockParcelFeature, ...]
    type: Literal["FeatureCollection"] = field(init=False, default="FeatureCollection")


class BlockParcelLayerQueryRepository(Protocol):
    def project_exists(self, *, project_id: uuid.UUID) -> bool: ...

    def list_runs(self, *, project_id: uuid.UUID) -> list[BlockParcelRunSummary]: ...

    def get_run_context(
        self, *, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> BlockParcelRunContext | None: ...

    def list_blocks(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[BlockParcelFeature]: ...

    def list_parcels(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[BlockParcelFeature]: ...


class BlockParcelLayerQueryService:
    """Validate block/parcel map reads and keep spatial SQL behind a repository port."""

    def __init__(self, repository: BlockParcelLayerQueryRepository) -> None:
        self._repository = repository

    def list_runs(self, *, project_id: uuid.UUID) -> tuple[BlockParcelRunSummary, ...]:
        if not self._repository.project_exists(project_id=project_id):
            raise BlockParcelLayerProjectNotFoundError(project_id)
        return tuple(self._repository.list_runs(project_id=project_id))

    def get_blocks(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_BLOCK_PARCEL_LAYER_LIMIT,
    ) -> BlockParcelQueryResult:
        return self._get_features(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox_text,
            limit=limit,
            kind="blocks",
        )

    def get_parcels(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_BLOCK_PARCEL_LAYER_LIMIT,
    ) -> BlockParcelQueryResult:
        return self._get_features(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox_text,
            limit=limit,
            kind="parcels",
        )

    def _get_features(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int,
        kind: Literal["blocks", "parcels"],
    ) -> BlockParcelQueryResult:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise BlockParcelLayerQueryError("limit must be an integer")
        if limit < 1 or limit > MAX_BLOCK_PARCEL_LAYER_LIMIT:
            raise BlockParcelLayerQueryError(
                f"limit must be between 1 and {MAX_BLOCK_PARCEL_LAYER_LIMIT}"
            )
        try:
            bbox = SourceLayerBbox.parse(bbox_text)
        except SourceLayerQueryError as exc:
            raise BlockParcelLayerQueryError(str(exc)) from exc

        context = self._repository.get_run_context(project_id=project_id, run_id=run_id)
        if context is None:
            raise BlockParcelLayerRunNotFoundError(project_id, run_id)

        if kind == "blocks":
            features = self._repository.list_blocks(
                run_id=run_id,
                working_srid=context.working_srid,
                bbox=bbox,
                limit=limit + 1,
            )
        else:
            features = self._repository.list_parcels(
                run_id=run_id,
                working_srid=context.working_srid,
                bbox=bbox,
                limit=limit + 1,
            )
        truncated = len(features) > limit
        return BlockParcelQueryResult(
            project_id=project_id,
            run_id=run_id,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=context.working_srid,
            limit=limit,
            truncated=truncated,
            features=tuple(features[:limit]),
        )
