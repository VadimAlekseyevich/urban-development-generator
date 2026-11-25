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

DEFAULT_INFRASTRUCTURE_LAYER_LIMIT = 1500
MAX_INFRASTRUCTURE_LAYER_LIMIT = 5000

type InfrastructureOrigin = Literal["existing", "generated"]


class InfrastructureLayerQueryError(ValueError):
    """Raised when an infrastructure viewport request is invalid."""


class InfrastructureLayerProjectNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        super().__init__(f"project {project_id} was not found")


class InfrastructureLayerRunNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.run_id = run_id
        super().__init__(
            f"infrastructure run {run_id} was not found in project {project_id}"
        )


class InfrastructureReadModelNotReadyError(LookupError):
    def __init__(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.run_id = run_id
        super().__init__(
            f"infrastructure read model is not ready for run {run_id} "
            f"in project {project_id}"
        )


@dataclass(frozen=True, slots=True)
class InfrastructureRunSummary:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    existing_facility_count: int
    generated_facility_count: int
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class InfrastructureRunContext:
    project_id: uuid.UUID
    run_id: uuid.UUID
    working_srid: int
    status: str


@dataclass(frozen=True, slots=True)
class InfrastructureAgeCoverageSummary:
    demographic_group: str
    population: float
    covered_population: float
    coverage_ratio: float


@dataclass(frozen=True, slots=True)
class InfrastructureRawMetricSummary:
    metric_id: str
    scalar_value: float | None
    age_coverage: tuple[InfrastructureAgeCoverageSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class InfrastructureFacilityAccessibilitySummary:
    origin: InfrastructureOrigin
    infrastructure_type_code: str
    max_network_distance_m: float
    reachable_demand_count: int
    nearest_distance_m: float | None
    farthest_distance_m: float | None
    facility_id: str | None = None
    source_ref: str | None = None
    source_feature_id: str | None = None
    candidate_id: str | None = None
    acceptance_index: int | None = None
    capacity: float | None = None
    network_snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class InfrastructureMetricsSummary:
    project_id: uuid.UUID
    run_id: uuid.UUID
    read_model_version: str
    scenario_version: str
    scenario_fingerprint: str
    raw_metrics: tuple[InfrastructureRawMetricSummary, ...]
    diagnostics: dict[str, int]
    facility_accessibility: tuple[
        InfrastructureFacilityAccessibilitySummary,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class InfrastructureFeature:
    id: uuid.UUID
    origin: InfrastructureOrigin
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class InfrastructureQueryResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    truncated: bool
    features: tuple[InfrastructureFeature, ...]
    type: Literal["FeatureCollection"] = field(
        init=False,
        default="FeatureCollection",
    )


@dataclass(frozen=True, slots=True)
class InfrastructureDemandFeature:
    id: uuid.UUID
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class InfrastructureDemandQueryResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    truncated: bool
    features: tuple[InfrastructureDemandFeature, ...]
    type: Literal["FeatureCollection"] = field(
        init=False,
        default="FeatureCollection",
    )


class InfrastructureLayerQueryRepository(Protocol):
    """Read-only port for one run's persisted S10 presentation state."""

    def project_exists(self, *, project_id: uuid.UUID) -> bool: ...

    def list_runs(
        self,
        *,
        project_id: uuid.UUID,
    ) -> list[InfrastructureRunSummary]: ...

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> InfrastructureRunContext | None: ...

    def read_model_exists(self, *, run_id: uuid.UUID) -> bool: ...

    def get_metrics(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> InfrastructureMetricsSummary | None: ...

    def list_facilities(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
        origin: InfrastructureOrigin | None = None,
    ) -> list[InfrastructureFeature]: ...

    def list_demand_blocks(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[InfrastructureDemandFeature]: ...


class InfrastructureLayerQueryService:
    """Validate bounded infrastructure reads and keep PostGIS SQL in an adapter."""

    def __init__(self, repository: InfrastructureLayerQueryRepository) -> None:
        self._repository = repository

    def list_runs(
        self,
        *,
        project_id: uuid.UUID,
    ) -> tuple[InfrastructureRunSummary, ...]:
        if not self._repository.project_exists(project_id=project_id):
            raise InfrastructureLayerProjectNotFoundError(project_id)
        return tuple(self._repository.list_runs(project_id=project_id))

    def get_metrics(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> InfrastructureMetricsSummary:
        self._require_run(project_id=project_id, run_id=run_id)
        metrics = self._repository.get_metrics(
            project_id=project_id,
            run_id=run_id,
        )
        if metrics is None:
            raise InfrastructureReadModelNotReadyError(project_id, run_id)
        return metrics

    def get_facilities(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_INFRASTRUCTURE_LAYER_LIMIT,
        origin: InfrastructureOrigin | None = None,
    ) -> InfrastructureQueryResult:
        bbox = self._bbox_and_limit(bbox_text=bbox_text, limit=limit)
        if origin not in (None, "existing", "generated"):
            raise InfrastructureLayerQueryError(
                "origin must be existing or generated"
            )
        context = self._require_run(project_id=project_id, run_id=run_id)

        features = self._repository.list_facilities(
            run_id=run_id,
            working_srid=context.working_srid,
            bbox=bbox,
            limit=limit + 1,
            origin=origin,
        )
        truncated = len(features) > limit
        return InfrastructureQueryResult(
            project_id=project_id,
            run_id=run_id,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=context.working_srid,
            limit=limit,
            truncated=truncated,
            features=tuple(features[:limit]),
        )

    def get_demand_blocks(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_INFRASTRUCTURE_LAYER_LIMIT,
    ) -> InfrastructureDemandQueryResult:
        bbox = self._bbox_and_limit(bbox_text=bbox_text, limit=limit)
        context = self._require_run(project_id=project_id, run_id=run_id)
        if not self._repository.read_model_exists(run_id=run_id):
            raise InfrastructureReadModelNotReadyError(project_id, run_id)

        features = self._repository.list_demand_blocks(
            run_id=run_id,
            working_srid=context.working_srid,
            bbox=bbox,
            limit=limit + 1,
        )
        truncated = len(features) > limit
        return InfrastructureDemandQueryResult(
            project_id=project_id,
            run_id=run_id,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=context.working_srid,
            limit=limit,
            truncated=truncated,
            features=tuple(features[:limit]),
        )

    def _require_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> InfrastructureRunContext:
        context = self._repository.get_run_context(
            project_id=project_id,
            run_id=run_id,
        )
        if context is None:
            raise InfrastructureLayerRunNotFoundError(project_id, run_id)
        return context

    @staticmethod
    def _bbox_and_limit(
        *,
        bbox_text: str,
        limit: int,
    ) -> SourceLayerBbox:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise InfrastructureLayerQueryError("limit must be an integer")
        if limit < 1 or limit > MAX_INFRASTRUCTURE_LAYER_LIMIT:
            raise InfrastructureLayerQueryError(
                f"limit must be between 1 and {MAX_INFRASTRUCTURE_LAYER_LIMIT}"
            )
        try:
            return SourceLayerBbox.parse(bbox_text)
        except SourceLayerQueryError as exc:
            raise InfrastructureLayerQueryError(str(exc)) from exc
