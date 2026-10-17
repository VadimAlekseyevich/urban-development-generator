import asyncio
import uuid
from datetime import timedelta
from functools import lru_cache
from typing import Any, cast

from arq import Retry

from backend.app.adapters import get_runtime_artifact_store
from backend.app.core.config import settings
from backend.app.application.generation import (
    GenerationClaimDisposition,
    GenerationExecutionError,
    GenerationJobService,
    GenerationRetryScheduled,
    GenerationRuntimeFactory,
)
from backend.app.application.geojson_exports import (
    GeoJsonExportJobService,
    GeoJsonExportRunStatus,
)
from backend.app.application.geopackage_exports import (
    GeoPackageExportJobService,
    GeoPackageExportRunStatus,
)
from backend.app.application.ingest import IngestJobRunStatus, IngestJobService
from backend.app.db.artifact_gc import SqlAlchemyArtifactGc
from backend.app.db.generation_state import SqlAlchemyGenerationStateStore
from backend.app.db.geojson_export_page_reader import SqlAlchemyGeoJsonExportPageReader
from backend.app.db.geojson_export_repository import SqlAlchemyGeoJsonExportRepository
from backend.app.db.geopackage_export_page_reader import SqlAlchemyGeoPackageExportPageReader
from backend.app.db.geopackage_export_repository import SqlAlchemyGeoPackageExportRepository
from backend.app.db.ingest_job_repository import SqlAlchemyIngestJobRepository
from backend.app.services.geojson_export import StreamingGeoJsonExportPipeline
from backend.app.services.geopackage_export import StreamingGeoPackageExportPipeline
from backend.app.services.ingest_pipeline import DatasetIngestPipeline
from core.urban_generator.domain.errors import ConfigError, TransientError

_MAX_RETRY_DELAY_SECONDS = 300


class IngestTaskFailed(RuntimeError):
    """Raised after authoritative DB state records a non-retryable ingest failure."""


def _build_ingest_job_service() -> IngestJobService:
    store = get_runtime_artifact_store()
    return IngestJobService(
        repository=SqlAlchemyIngestJobRepository(),
        pipeline=DatasetIngestPipeline(store=store),
    )


def _retry_delay_seconds(attempt_count: int) -> int:
    exponent = max(0, min(attempt_count - 1, 4))
    return min(30 * (2**exponent), _MAX_RETRY_DELAY_SECONDS)


async def run_ingest(
    ctx: dict[str, Any],
    job_id: str,
    dataset_version_id: str,
) -> dict[str, object]:
    """Run one DB-authoritative DatasetVersion ingest attempt."""
    parsed_job_id = uuid.UUID(job_id)
    parsed_version_id = uuid.UUID(dataset_version_id)
    override = ctx.get("ingest_job_service")
    service = (
        cast(IngestJobService, override)
        if override is not None
        else _build_ingest_job_service()
    )

    try:
        result = await asyncio.to_thread(
            service.run,
            job_id=parsed_job_id,
            dataset_version_id=parsed_version_id,
        )
    except TransientError as exc:
        raise Retry(defer=_retry_delay_seconds(1)) from exc

    if result.status is IngestJobRunStatus.FAILED:
        if result.retryable:
            raise Retry(defer=_retry_delay_seconds(result.attempt_count))
        raise IngestTaskFailed(
            f"ingest job {job_id} failed permanently on attempt {result.attempt_count}"
        )
    if result.status is IngestJobRunStatus.EXHAUSTED:
        raise IngestTaskFailed(f"ingest job {job_id} exhausted its retry budget")

    return {
        "job_id": str(result.job_id),
        "dataset_version_id": str(result.dataset_version_id),
        "status": result.status.value,
        "attempt_count": result.attempt_count,
        "max_attempts": result.max_attempts,
        "details": dict(result.details or {}),
    }


class GeoJsonExportTaskFailed(RuntimeError):
    """Authoritative export state recorded a terminal worker failure."""


def _build_geojson_export_job_service() -> GeoJsonExportJobService:
    store = get_runtime_artifact_store()
    return GeoJsonExportJobService(
        repository=SqlAlchemyGeoJsonExportRepository(),
        pipeline=StreamingGeoJsonExportPipeline(
            store=store,
            page_reader=SqlAlchemyGeoJsonExportPageReader(),
        ),
    )


async def run_geojson_export(
    ctx: dict[str, Any],
    job_id: str,
) -> dict[str, object]:
    """Execute one persisted single-layer export outside the HTTP lifecycle."""
    parsed_job_id = uuid.UUID(job_id)
    override = ctx.get("geojson_export_service")
    service = (
        cast(GeoJsonExportJobService, override)
        if override is not None
        else _build_geojson_export_job_service()
    )
    result = await asyncio.to_thread(service.run, job_id=parsed_job_id)
    if result.status is GeoJsonExportRunStatus.FAILED:
        if result.retryable:
            raise Retry(defer=_retry_delay_seconds(result.attempt_count))
        raise GeoJsonExportTaskFailed(
            f"GeoJSON export {job_id} failed permanently on attempt "
            f"{result.attempt_count}"
        )
    if result.status is GeoJsonExportRunStatus.EXHAUSTED:
        raise GeoJsonExportTaskFailed(
            f"GeoJSON export {job_id} exhausted its retry budget"
        )
    return {
        "job_id": str(result.job_id),
        "status": result.status.value,
        "attempt_count": result.attempt_count,
        "max_attempts": result.max_attempts,
        "feature_count": result.feature_count,
    }


class GeoPackageExportTaskFailed(RuntimeError):
    """Authoritative GeoPackage export state recorded a terminal worker failure."""


def _build_geopackage_export_job_service() -> GeoPackageExportJobService:
    store = get_runtime_artifact_store()
    return GeoPackageExportJobService(
        repository=SqlAlchemyGeoPackageExportRepository(),
        pipeline=StreamingGeoPackageExportPipeline(
            store=store,
            page_reader=SqlAlchemyGeoPackageExportPageReader(),
        ),
    )


async def run_geopackage_export(
    ctx: dict[str, Any],
    job_id: str,
) -> dict[str, object]:
    """Execute one persisted multi-layer GeoPackage export."""
    parsed_job_id = uuid.UUID(job_id)
    override = ctx.get("geopackage_export_service")
    service = (
        cast(GeoPackageExportJobService, override)
        if override is not None
        else _build_geopackage_export_job_service()
    )
    result = await asyncio.to_thread(service.run, job_id=parsed_job_id)
    if result.status is GeoPackageExportRunStatus.FAILED:
        if result.retryable:
            raise Retry(defer=_retry_delay_seconds(result.attempt_count))
        raise GeoPackageExportTaskFailed(
            f"GeoPackage export {job_id} failed permanently on attempt "
            f"{result.attempt_count}"
        )
    if result.status is GeoPackageExportRunStatus.EXHAUSTED:
        raise GeoPackageExportTaskFailed(
            f"GeoPackage export {job_id} exhausted its retry budget"
        )
    return {
        "job_id": str(result.job_id),
        "status": result.status.value,
        "attempt_count": result.attempt_count,
        "max_attempts": result.max_attempts,
        "layer_counts": (
            [
                {
                    "layer_id": value.layer_id,
                    "feature_count": value.feature_count,
                }
                for value in result.layer_counts
            ]
            if result.layer_counts is not None
            else None
        ),
        "total_feature_count": result.total_feature_count,
    }


class GenerationTaskConfigurationError(RuntimeError):
    """The worker has no explicit typed stage/runtime composition for generation."""


class GenerationTaskFailed(RuntimeError):
    """The DB-authoritative generation attempt failed; never report a false success."""


def _fail_unconfigured_generation(
    run_id: uuid.UUID,
) -> GenerationClaimDisposition:
    """Never leave a DB-authoritative generation job queued on missing composition.

    Only the real persisted state store may transition the job/run; no fake
    completed stages or successful outputs are generated.
    """
    state_store = SqlAlchemyGenerationStateStore()
    disposition = state_store.claim(run_id=run_id)
    if disposition is GenerationClaimDisposition.STARTED:
        state_store.fail_run(
            run_id=run_id,
            stage_name=None,
            error=ConfigError(
                "generation runtime is not configured in this worker",
                details={"component": "generation_runtime_factory"},
            ),
        )
    return disposition


async def run_generation(ctx: dict[str, Any], run_id: str) -> dict[str, object]:
    """Execute a configured canonical Stage DAG in a worker thread, not HTTP."""
    parsed_id = uuid.UUID(run_id)
    service_override = ctx.get("generation_job_service")
    if service_override is not None:
        service = cast(GenerationJobService, service_override)
    else:
        factory = ctx.get("generation_runtime_factory")
        if factory is None:
            try:
                disposition = await asyncio.to_thread(
                    _fail_unconfigured_generation, parsed_id
                )
            except GenerationRetryScheduled as exc:
                raise Retry(defer=exc.delay_seconds) from exc
            if disposition is not GenerationClaimDisposition.STARTED:
                # A replay of an already completed, running or cancelled job
                # must not overwrite its existing persisted state.
                return {
                    "run_id": str(parsed_id),
                    "status": disposition.value,
                    "succeeded_stages": [],
                    "skipped_stages": [],
                }
            raise GenerationTaskConfigurationError(
                "generation_runtime_factory must supply typed stage/config/input composition"
            )
        service = GenerationJobService(
            state_store=SqlAlchemyGenerationStateStore(),
            runtime_factory=cast(GenerationRuntimeFactory, factory),
        )

    try:
        result = await asyncio.to_thread(service.run, run_id=parsed_id)
    except GenerationRetryScheduled as exc:
        raise Retry(defer=exc.delay_seconds) from exc
    except GenerationExecutionError as exc:
        raise GenerationTaskFailed(f"generation run {parsed_id} failed") from exc

    return {
        "run_id": str(result.run_id),
        "status": result.status,
        "succeeded_stages": list(result.succeeded_stages),
        "skipped_stages": list(result.skipped_stages),
    }


@lru_cache(maxsize=1)
def _build_artifact_gc() -> SqlAlchemyArtifactGc:
    """Reuse one local scan cursor throughout this worker process."""
    return SqlAlchemyArtifactGc(store=get_runtime_artifact_store())


async def gc_orphan_artifacts(ctx: dict[str, Any]) -> dict[str, int]:
    """Hourly bounded cleanup of aged run-stage blobs and temporary DB rows."""
    override = ctx.get("artifact_gc")
    collector = (
        cast(SqlAlchemyArtifactGc, override)
        if override is not None
        else _build_artifact_gc()
    )
    # Preserve injected collectors' existing no-arg integration seam.
    if override is not None:
        result = await asyncio.to_thread(collector.collect)
    else:
        result = await asyncio.to_thread(
            collector.collect,
            min_age=timedelta(hours=settings.artifact_gc_min_age_hours),
            max_batch=settings.artifact_gc_max_batch,
            max_scan=settings.artifact_gc_max_scan,
            dry_run=settings.artifact_gc_dry_run,
        )
    return {
        "expired_rows": result.expired_rows,
        "untracked_keys": result.untracked_keys,
        "examined_candidates": result.examined_candidates,
    }
