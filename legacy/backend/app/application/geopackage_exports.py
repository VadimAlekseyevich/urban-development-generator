from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from backend.app.application.layer_catalog import (
    CANONICAL_LAYER_DEFINITIONS,
    LayerDeliveryKind,
    LayerOwnerScope,
)
from core.urban_generator.domain import ArtifactRef, ArtifactStat
from core.urban_generator.domain.errors import TransientError, UrbanGeneratorError

GEOPACKAGE_EXPORT_SCHEMA_VERSION = "geopackage-export-v1"
GEOPACKAGE_EXPORT_JOB_TYPE = "geopackage_export"
GEOPACKAGE_EXPORT_CONTENT_TYPE = "application/geopackage+sqlite3"
MAX_GEOPACKAGE_EXPORT_LAYERS = 15
DEFAULT_GEOPACKAGE_MAX_FEATURES_PER_LAYER = 100_000
MAX_GEOPACKAGE_FEATURES_PER_LAYER = 100_000
DEFAULT_GEOPACKAGE_MAX_TOTAL_FEATURES = 250_000
MAX_GEOPACKAGE_TOTAL_FEATURES = 500_000

_TABLE_BACKED_DEFINITIONS = tuple(
    definition
    for definition in CANONICAL_LAYER_DEFINITIONS
    if definition.delivery_kind is LayerDeliveryKind.BBOX_GEOJSON
    and definition.layer_id != "validation.violations"
)
_TABLE_BACKED_BY_ID = {
    definition.layer_id: definition for definition in _TABLE_BACKED_DEFINITIONS
}
_CANONICAL_ORDER = {
    definition.layer_id: index for index, definition in enumerate(_TABLE_BACKED_DEFINITIONS)
}


class GeoPackageExportError(ValueError):
    """Invalid canonical layer selection, owner or bounded export request."""


class GeoPackageExportNotFoundError(LookupError):
    """Project, owner, job or artifact does not exist in the requested scope."""


class GeoPackageExportConflictError(RuntimeError):
    """Persisted owner/job state is not exportable yet."""


@dataclass(frozen=True, slots=True)
class GeoPackageExportSpec:
    layer_ids: tuple[str, ...]
    dataset_version_id: uuid.UUID | None
    run_id: uuid.UUID | None
    bbox: tuple[float, float, float, float]
    max_features_per_layer: int = DEFAULT_GEOPACKAGE_MAX_FEATURES_PER_LAYER
    max_total_features: int = DEFAULT_GEOPACKAGE_MAX_TOTAL_FEATURES

    def __post_init__(self) -> None:
        if not isinstance(self.layer_ids, tuple):
            raise GeoPackageExportError("layer_ids must be an immutable tuple")
        if not 1 <= len(self.layer_ids) <= MAX_GEOPACKAGE_EXPORT_LAYERS:
            raise GeoPackageExportError(
                f"layer_ids must contain 1–{MAX_GEOPACKAGE_EXPORT_LAYERS} layers"
            )
        if any(not isinstance(layer_id, str) for layer_id in self.layer_ids):
            raise GeoPackageExportError("layer_ids must contain strings")
        if len(set(self.layer_ids)) != len(self.layer_ids):
            raise GeoPackageExportError("layer_ids must not contain duplicates")
        unknown = [layer_id for layer_id in self.layer_ids if layer_id not in _TABLE_BACKED_BY_ID]
        if unknown:
            raise GeoPackageExportError(
                "GeoPackage export supports only the 15 table-backed catalog layers"
            )

        canonical = tuple(sorted(self.layer_ids, key=_CANONICAL_ORDER.__getitem__))
        object.__setattr__(self, "layer_ids", canonical)

        needs_dataset = any(
            _TABLE_BACKED_BY_ID[layer_id].owner_scope is LayerOwnerScope.DATASET_VERSION
            for layer_id in canonical
        )
        needs_run = any(
            _TABLE_BACKED_BY_ID[layer_id].owner_scope is LayerOwnerScope.RUN
            for layer_id in canonical
        )
        if needs_dataset != isinstance(self.dataset_version_id, uuid.UUID):
            raise GeoPackageExportError(
                "dataset_version_id must be set exactly when source layers are selected"
            )
        if needs_run != isinstance(self.run_id, uuid.UUID):
            raise GeoPackageExportError(
                "run_id must be set exactly when run-owned layers are selected"
            )

        if (
            isinstance(self.max_features_per_layer, bool)
            or not isinstance(self.max_features_per_layer, int)
            or not 1
            <= self.max_features_per_layer
            <= MAX_GEOPACKAGE_FEATURES_PER_LAYER
        ):
            raise GeoPackageExportError(
                "max_features_per_layer must be "
                f"1–{MAX_GEOPACKAGE_FEATURES_PER_LAYER}"
            )
        if (
            isinstance(self.max_total_features, bool)
            or not isinstance(self.max_total_features, int)
            or not 1 <= self.max_total_features <= MAX_GEOPACKAGE_TOTAL_FEATURES
        ):
            raise GeoPackageExportError(
                f"max_total_features must be 1–{MAX_GEOPACKAGE_TOTAL_FEATURES}"
            )

        if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
            raise GeoPackageExportError("bbox must contain west,south,east,north")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in self.bbox
        ):
            raise GeoPackageExportError("bbox coordinates must be finite numbers")
        west, south, east, north = (float(value) for value in self.bbox)
        if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
            raise GeoPackageExportError(
                "bbox must be a valid non-wrapping EPSG:4326 extent"
            )
        object.__setattr__(self, "bbox", (west, south, east, north))

    @property
    def idempotency_key(self) -> str:
        payload = {
            "schema": GEOPACKAGE_EXPORT_SCHEMA_VERSION,
            "layer_ids": list(self.layer_ids),
            "dataset_version_id": (
                str(self.dataset_version_id) if self.dataset_version_id else None
            ),
            "run_id": str(self.run_id) if self.run_id else None,
            "bbox": list(self.bbox),
            "max_features_per_layer": self.max_features_per_layer,
            "max_total_features": self.max_total_features,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return "geopackage:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class GeoPackageLayerCount:
    layer_id: str
    feature_count: int

    def __post_init__(self) -> None:
        if self.layer_id not in _TABLE_BACKED_BY_ID:
            raise GeoPackageExportError("layer count has unknown canonical layer")
        if (
            isinstance(self.feature_count, bool)
            or not isinstance(self.feature_count, int)
            or self.feature_count < 0
        ):
            raise GeoPackageExportError("feature_count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class GeoPackageExportArtifactSnapshot:
    artifact_id: uuid.UUID
    filename: str
    size_bytes: int
    checksum: str
    content_type: str


@dataclass(frozen=True, slots=True)
class GeoPackageExportSnapshot:
    job_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    attempt_count: int
    max_attempts: int
    layer_ids: tuple[str, ...]
    dataset_version_id: uuid.UUID | None
    run_id: uuid.UUID | None
    bbox: tuple[float, float, float, float]
    max_features_per_layer: int
    max_total_features: int
    artifact: GeoPackageExportArtifactSnapshot | None
    layer_counts: tuple[GeoPackageLayerCount, ...] | None
    error_class: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    schema_version: str = GEOPACKAGE_EXPORT_SCHEMA_VERSION

    @property
    def total_feature_count(self) -> int | None:
        if self.layer_counts is None:
            return None
        return sum(value.feature_count for value in self.layer_counts)


class GeoPackageExportRepository(Protocol):
    def create(
        self,
        *,
        project_id: uuid.UUID,
        spec: GeoPackageExportSpec,
    ) -> GeoPackageExportSnapshot: ...

    def get(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> GeoPackageExportSnapshot: ...

    def ready_artifact_ref(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> tuple[ArtifactRef, str, int]: ...


class GeoPackageExportService:
    """HTTP-facing multi-layer export creation/read contract."""

    def __init__(self, repository: GeoPackageExportRepository) -> None:
        self._repository = repository

    def create(
        self,
        *,
        project_id: uuid.UUID,
        spec: GeoPackageExportSpec,
    ) -> GeoPackageExportSnapshot:
        if not isinstance(project_id, uuid.UUID):
            raise TypeError("project_id must be UUID")
        return self._repository.create(project_id=project_id, spec=spec)

    def get(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> GeoPackageExportSnapshot:
        return self._repository.get(project_id=project_id, job_id=job_id)

    def ready_artifact_ref(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> tuple[ArtifactRef, str, int]:
        return self._repository.ready_artifact_ref(
            project_id=project_id,
            job_id=job_id,
        )


class GeoPackageExportClaimDisposition(StrEnum):
    STARTED = "started"
    ALREADY_SUCCEEDED = "already_succeeded"
    IN_PROGRESS = "in_progress"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class GeoPackageExportJobContext:
    job_id: uuid.UUID
    project_id: uuid.UUID
    spec: GeoPackageExportSpec
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class GeoPackageExportClaim:
    disposition: GeoPackageExportClaimDisposition
    attempt_count: int
    max_attempts: int
    context: GeoPackageExportJobContext | None = None


@dataclass(frozen=True, slots=True)
class GeoPackageExportPipelineResult:
    artifact: ArtifactStat
    layer_counts: tuple[GeoPackageLayerCount, ...]

    @property
    def total_feature_count(self) -> int:
        return sum(value.feature_count for value in self.layer_counts)


class GeoPackageExportExecutionRepository(Protocol):
    def claim(self, *, job_id: uuid.UUID) -> GeoPackageExportClaim: ...

    def complete(
        self,
        *,
        context: GeoPackageExportJobContext,
        result: GeoPackageExportPipelineResult,
    ) -> None: ...

    def fail(
        self,
        *,
        context: GeoPackageExportJobContext,
        error: UrbanGeneratorError,
    ) -> bool: ...


class GeoPackageExportPipeline(Protocol):
    def execute(
        self,
        context: GeoPackageExportJobContext,
    ) -> GeoPackageExportPipelineResult: ...


class GeoPackageExportRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    ALREADY_SUCCEEDED = "already_succeeded"
    IN_PROGRESS = "in_progress"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GeoPackageExportRunResult:
    job_id: uuid.UUID
    status: GeoPackageExportRunStatus
    attempt_count: int
    max_attempts: int
    retryable: bool = False
    layer_counts: tuple[GeoPackageLayerCount, ...] | None = None

    @property
    def total_feature_count(self) -> int | None:
        if self.layer_counts is None:
            return None
        return sum(value.feature_count for value in self.layer_counts)


class GeoPackageExportJobService:
    """Execute one retry-safe GeoPackage export attempt outside HTTP."""

    def __init__(
        self,
        *,
        repository: GeoPackageExportExecutionRepository,
        pipeline: GeoPackageExportPipeline,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline

    def run(self, *, job_id: uuid.UUID) -> GeoPackageExportRunResult:
        claim = self._repository.claim(job_id=job_id)
        if claim.disposition is GeoPackageExportClaimDisposition.ALREADY_SUCCEEDED:
            return GeoPackageExportRunResult(
                job_id=job_id,
                status=GeoPackageExportRunStatus.ALREADY_SUCCEEDED,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )
        if claim.disposition is GeoPackageExportClaimDisposition.IN_PROGRESS:
            return GeoPackageExportRunResult(
                job_id=job_id,
                status=GeoPackageExportRunStatus.IN_PROGRESS,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )
        if claim.disposition is GeoPackageExportClaimDisposition.EXHAUSTED:
            return GeoPackageExportRunResult(
                job_id=job_id,
                status=GeoPackageExportRunStatus.EXHAUSTED,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )

        context = claim.context
        if context is None:
            raise RuntimeError("started export claim must include context")
        try:
            result = self._pipeline.execute(context)
            self._repository.complete(context=context, result=result)
        except UrbanGeneratorError as caught_error:
            retryable = self._repository.fail(context=context, error=caught_error)
            return GeoPackageExportRunResult(
                job_id=job_id,
                status=GeoPackageExportRunStatus.FAILED,
                attempt_count=context.attempt_count,
                max_attempts=context.max_attempts,
                retryable=retryable,
            )
        except Exception as exc:
            unexpected_error = TransientError(
                "GeoPackage export failed unexpectedly",
                details={"exception_type": type(exc).__name__},
            )
            retryable = self._repository.fail(
                context=context,
                error=unexpected_error,
            )
            return GeoPackageExportRunResult(
                job_id=job_id,
                status=GeoPackageExportRunStatus.FAILED,
                attempt_count=context.attempt_count,
                max_attempts=context.max_attempts,
                retryable=retryable,
            )
        return GeoPackageExportRunResult(
            job_id=job_id,
            status=GeoPackageExportRunStatus.SUCCEEDED,
            attempt_count=context.attempt_count,
            max_attempts=context.max_attempts,
            layer_counts=result.layer_counts,
        )


def geopackage_export_filename(job_id: uuid.UUID) -> str:
    return f"urban-layers-{str(job_id)[:8]}.gpkg"


def geopackage_layer_name(layer_id: str) -> str:
    if layer_id not in _TABLE_BACKED_BY_ID:
        raise GeoPackageExportError("unknown table-backed layer")
    return layer_id.replace(".", "_")
