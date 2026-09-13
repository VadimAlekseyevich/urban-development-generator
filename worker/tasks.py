import asyncio
import uuid
from typing import Any, cast

from arq import Retry

from backend.app.adapters import LocalArtifactStore
from backend.app.application.ingest import IngestJobRunStatus, IngestJobService
from backend.app.core.config import settings
from backend.app.db.ingest_job_repository import SqlAlchemyIngestJobRepository
from backend.app.services.ingest_pipeline import DatasetIngestPipeline
from core.urban_generator.domain.errors import TransientError

_MAX_RETRY_DELAY_SECONDS = 300


class IngestTaskFailed(RuntimeError):
    """Raised after authoritative DB state records a non-retryable ingest failure."""


def _build_ingest_job_service() -> IngestJobService:
    store = LocalArtifactStore(settings.storage_root)
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


async def run_generation(ctx: dict[str, Any], run_id: str) -> dict[str, str]:
    """Entry point for long-running GIS generation jobs."""
    parsed_id = uuid.UUID(run_id)
    return {"run_id": str(parsed_id), "status": "accepted"}
