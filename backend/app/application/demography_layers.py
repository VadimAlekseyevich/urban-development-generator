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

DEFAULT_DEMOGRAPHY_LAYER_LIMIT = 1500
MAX_DEMOGRAPHY_LAYER_LIMIT = 5000


class DemographyLayerQueryError(ValueError):
    """Raised when a demographic viewport request is invalid."""


class DemographyLayerProjectNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID) -> None:
        super().__init__(f"project {project_id} was not found")


class DemographyLayerRunNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        super().__init__(
            f"demography run {run_id} was not found in project {project_id}"
        )


@dataclass(frozen=True, slots=True)
class DemographyAgeGroupMetric:
    code: str
    min_age: int
    max_age: int | None
    residents: int
    share: float


@dataclass(frozen=True, slots=True)
class DemographyRunSummary:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    block_count: int
    population: int
    population_density_per_km2: float
    jobs_estimate: float
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class DemographyRunContext:
    project_id: uuid.UUID
    run_id: uuid.UUID
    working_srid: int
    status: str


@dataclass(frozen=True, slots=True)
class DemographyMetricsSummary:
    project_id: uuid.UUID
    run_id: uuid.UUID
    scenario_version: str
    scenario_fingerprint: str
    employment_config_version: str
    employment_config_fingerprint: str
    block_count: int
    area_m2: float
    population: int
    population_density_per_km2: float
    jobs_estimate: float
    age_groups: tuple[DemographyAgeGroupMetric, ...]


@dataclass(frozen=True, slots=True)
class DemographyFeature:
    id: uuid.UUID
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class DemographyQueryResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    truncated: bool
    features: tuple[DemographyFeature, ...]
    type: Literal["FeatureCollection"] = field(
        init=False,
        default="FeatureCollection",
    )


class DemographyLayerQueryRepository(Protocol):
    def project_exists(self, *, project_id: uuid.UUID) -> bool: ...

    def list_runs(
        self,
        *,
        project_id: uuid.UUID,
    ) -> list[DemographyRunSummary]: ...

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> DemographyRunContext | None: ...

    def get_metrics(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> DemographyMetricsSummary | None: ...

    def list_blocks(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[DemographyFeature]: ...


class DemographyLayerQueryService:
    def __init__(self, repository: DemographyLayerQueryRepository) -> None:
        self._repository = repository

    def list_runs(
        self,
        *,
        project_id: uuid.UUID,
    ) -> tuple[DemographyRunSummary, ...]:
        if not self._repository.project_exists(project_id=project_id):
            raise DemographyLayerProjectNotFoundError(project_id)
        return tuple(self._repository.list_runs(project_id=project_id))

    def get_metrics(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> DemographyMetricsSummary:
        metrics = self._repository.get_metrics(
            project_id=project_id,
            run_id=run_id,
        )
        if metrics is None:
            raise DemographyLayerRunNotFoundError(project_id, run_id)
        return metrics

    def get_blocks(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_DEMOGRAPHY_LAYER_LIMIT,
    ) -> DemographyQueryResult:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise DemographyLayerQueryError("limit must be an integer")
        if limit < 1 or limit > MAX_DEMOGRAPHY_LAYER_LIMIT:
            raise DemographyLayerQueryError(
                f"limit must be between 1 and {MAX_DEMOGRAPHY_LAYER_LIMIT}"
            )
        try:
            bbox = SourceLayerBbox.parse(bbox_text)
        except SourceLayerQueryError as exc:
            raise DemographyLayerQueryError(str(exc)) from exc

        context = self._repository.get_run_context(
            project_id=project_id,
            run_id=run_id,
        )
        if context is None:
            raise DemographyLayerRunNotFoundError(project_id, run_id)

        features = self._repository.list_blocks(
            run_id=run_id,
            working_srid=context.working_srid,
            bbox=bbox,
            limit=limit + 1,
        )
        truncated = len(features) > limit
        return DemographyQueryResult(
            project_id=project_id,
            run_id=run_id,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=context.working_srid,
            limit=limit,
            truncated=truncated,
            features=tuple(features[:limit]),
        )
