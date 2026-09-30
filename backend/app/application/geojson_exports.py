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

GEOJSON_EXPORT_SCHEMA_VERSION = "geojson-export-v1"
GEOJSON_EXPORT_JOB_TYPE = "geojson_export"
GEOJSON_EXPORT_CONTENT_TYPE = "application/geo+json"
DEFAULT_GEOJSON_EXPORT_MAX_FEATURES = 100_000
MAX_GEOJSON_EXPORT_FEATURES = 100_000


class GeoJsonExportError(ValueError):
    """Invalid canonical layer, owner or bounded export request."""


class GeoJsonExportNotFoundError(LookupError):
    """Project, owner, job or artifact does not exist in the requested scope."""


class GeoJsonExportConflictError(RuntimeError):
    """Persisted owner/job state is not exportable yet."""


@dataclass(frozen=True, slots=True)
class GeoJsonExportSpec:
    layer_id: str
    dataset_version_id: uuid.UUID | None
    run_id: uuid.UUID | None
    bbox: tuple[float, float, float, float]
    max_features: int = DEFAULT_GEOJSON_EXPORT_MAX_FEATURES

    def __post_init__(self) -> None:
        definition = next(
            (
                value
                for value in CANONICAL_LAYER_DEFINITIONS
                if value.layer_id == self.layer_id
            ),
            None,
        )
        if definition is None:
            raise GeoJsonExportError("unknown canonical layer")
        if (
            definition.delivery_kind is not LayerDeliveryKind.BBOX_GEOJSON
            or self.layer_id == "validation.violations"
        ):
            raise GeoJsonExportError(
                "GeoJSON export supports only the 15 table-backed catalog layers"
            )
        if definition.owner_scope is LayerOwnerScope.DATASET_VERSION:
            if not isinstance(self.dataset_version_id, uuid.UUID) or self.run_id is not None:
                raise GeoJsonExportError(
                    "source export requires dataset_version_id only"
                )
        elif definition.owner_scope is LayerOwnerScope.RUN:
            if not isinstance(self.run_id, uuid.UUID) or self.dataset_version_id is not None:
                raise GeoJsonExportError("run export requires run_id only")
        else:
            raise GeoJsonExportError("layer is not dataset/run scoped")

        if (
            isinstance(self.max_features, bool)
            or not isinstance(self.max_features, int)
            or not 1 <= self.max_features <= MAX_GEOJSON_EXPORT_FEATURES
        ):
            raise GeoJsonExportError(
                f"max_features must be 1–{MAX_GEOJSON_EXPORT_FEATURES}"
            )
        if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
            raise GeoJsonExportError("bbox must contain west,south,east,north")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in self.bbox
        ):
            raise GeoJsonExportError("bbox coordinates must be finite numbers")
        west, south, east, north = (float(value) for value in self.bbox)
        if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
            raise GeoJsonExportError("bbox must be a valid non-wrapping EPSG:4326 extent")
        object.__setattr__(self, "bbox", (west, south, east, north))

    @property
    def owner_id(self) -> uuid.UUID:
        value = self.dataset_version_id or self.run_id
        assert value is not None
        return value

    @property
    def idempotency_key(self) -> str:
        payload = {
            "schema": GEOJSON_EXPORT_SCHEMA_VERSION,
            "layer_id": self.layer_id,
            "dataset_version_id": (
                str(self.dataset_version_id) if self.dataset_version_id else None
            ),
            "run_id": str(self.run_id) if self.run_id else None,
            "bbox": list(self.bbox),
            "max_features": self.max_features,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return "geojson:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class GeoJsonExportArtifactSnapshot:
    artifact_id: uuid.UUID
    filename: str
    size_bytes: int
    checksum: str
    content_type: str


@dataclass(frozen=True, slots=True)
class GeoJsonExportSnapshot:
    job_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    attempt_count: int
    max_attempts: int
    layer_id: str
    dataset_version_id: uuid.UUID | None
    run_id: uuid.UUID | None
    bbox: tuple[float, float, float, float]
    max_features: int
    artifact: GeoJsonExportArtifactSnapshot | None
    feature_count: int | None
    error_class: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    schema_version: str = GEOJSON_EXPORT_SCHEMA_VERSION


class GeoJsonExportRepository(Protocol):
    def create(
        self,
        *,
        project_id: uuid.UUID,
        spec: GeoJsonExportSpec,
    ) -> GeoJsonExportSnapshot: ...

    def get(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> GeoJsonExportSnapshot: ...

    def ready_artifact_ref(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> tuple[ArtifactRef, str, int]: ...


class GeoJsonExportService:
    """HTTP-facing creation/read contract; queue submission remains DB-outbox only."""

    def __init__(self, repository: GeoJsonExportRepository) -> None:
        self._repository = repository

    def create(
        self,
        *,
        project_id: uuid.UUID,
        spec: GeoJsonExportSpec,
    ) -> GeoJsonExportSnapshot:
        if not isinstance(project_id, uuid.UUID):
            raise TypeError("project_id must be UUID")
        return self._repository.create(project_id=project_id, spec=spec)

    def get(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> GeoJsonExportSnapshot:
        return self._repository.get(project_id=project_id, job_id=job_id)


class GeoJsonExportClaimDisposition(StrEnum):
    STARTED = "started"
    ALREADY_SUCCEEDED = "already_succeeded"
    IN_PROGRESS = "in_progress"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class GeoJsonExportJobContext:
    job_id: uuid.UUID
    project_id: uuid.UUID
    spec: GeoJsonExportSpec
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class GeoJsonExportClaim:
    disposition: GeoJsonExportClaimDisposition
    attempt_count: int
    max_attempts: int
    context: GeoJsonExportJobContext | None = None


@dataclass(frozen=True, slots=True)
class GeoJsonExportPipelineResult:
    artifact: ArtifactStat
    feature_count: int


class GeoJsonExportExecutionRepository(Protocol):
    def claim(self, *, job_id: uuid.UUID) -> GeoJsonExportClaim: ...

    def complete(
        self,
        *,
        context: GeoJsonExportJobContext,
        result: GeoJsonExportPipelineResult,
    ) -> None: ...

    def fail(
        self,
        *,
        context: GeoJsonExportJobContext,
        error: UrbanGeneratorError,
    ) -> bool: ...


class GeoJsonExportPipeline(Protocol):
    def execute(self, context: GeoJsonExportJobContext) -> GeoJsonExportPipelineResult: ...


class GeoJsonExportRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    ALREADY_SUCCEEDED = "already_succeeded"
    IN_PROGRESS = "in_progress"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GeoJsonExportRunResult:
    job_id: uuid.UUID
    status: GeoJsonExportRunStatus
    attempt_count: int
    max_attempts: int
    retryable: bool = False
    feature_count: int | None = None


class GeoJsonExportJobService:
    """Execute one retry-safe export attempt outside the HTTP request lifecycle."""

    def __init__(
        self,
        *,
        repository: GeoJsonExportExecutionRepository,
        pipeline: GeoJsonExportPipeline,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline

    def run(self, *, job_id: uuid.UUID) -> GeoJsonExportRunResult:
        claim = self._repository.claim(job_id=job_id)
        if claim.disposition is GeoJsonExportClaimDisposition.ALREADY_SUCCEEDED:
            return GeoJsonExportRunResult(
                job_id=job_id,
                status=GeoJsonExportRunStatus.ALREADY_SUCCEEDED,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )
        if claim.disposition is GeoJsonExportClaimDisposition.IN_PROGRESS:
            return GeoJsonExportRunResult(
                job_id=job_id,
                status=GeoJsonExportRunStatus.IN_PROGRESS,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )
        if claim.disposition is GeoJsonExportClaimDisposition.EXHAUSTED:
            return GeoJsonExportRunResult(
                job_id=job_id,
                status=GeoJsonExportRunStatus.EXHAUSTED,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )

        context = claim.context
        if context is None:
            raise RuntimeError("started export claim must include context")
        try:
            result = self._pipeline.execute(context)
            self._repository.complete(context=context, result=result)
        except UrbanGeneratorError as error:
            retryable = self._repository.fail(context=context, error=error)
            return GeoJsonExportRunResult(
                job_id=job_id,
                status=GeoJsonExportRunStatus.FAILED,
                attempt_count=context.attempt_count,
                max_attempts=context.max_attempts,
                retryable=retryable,
            )
        except Exception as exc:
            error = TransientError(
                "GeoJSON export failed unexpectedly",
                details={"exception_type": type(exc).__name__},
            )
            retryable = self._repository.fail(context=context, error=error)
            return GeoJsonExportRunResult(
                job_id=job_id,
                status=GeoJsonExportRunStatus.FAILED,
                attempt_count=context.attempt_count,
                max_attempts=context.max_attempts,
                retryable=retryable,
            )
        return GeoJsonExportRunResult(
            job_id=job_id,
            status=GeoJsonExportRunStatus.SUCCEEDED,
            attempt_count=context.attempt_count,
            max_attempts=context.max_attempts,
            feature_count=result.feature_count,
        )


def geojson_export_filename(layer_id: str, job_id: uuid.UUID) -> str:
    safe_layer = layer_id.replace(".", "-").replace("_", "-")
    return f"{safe_layer}-{str(job_id)[:8]}.geojson"
