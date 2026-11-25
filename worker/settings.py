"""Isolated ARQ queue workers with explicit CLI-discoverable connection settings.

ARQ inspects each concrete settings class __dict__, so redis_settings and
job_timeout MUST be declared on every concrete role (not inherited only).
"""

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from backend.app.adapters.artifact_store_factory import get_runtime_artifact_store
from backend.app.adapters.complete_generation_runtime import build_complete_generation_service
from backend.app.core.config import settings
from worker.queues import LEGACY_QUEUE, REDIS_QUEUES
from worker.tasks import (
    gc_orphan_artifacts,
    run_generation,
    run_geojson_export,
    run_geopackage_export,
    run_ingest,
)

_REDIS_SETTINGS = RedisSettings.from_dsn(settings.redis_url)
_JOB_TIMEOUT = 60 * 60


async def configure_generation(ctx: dict[str, Any]) -> None:
    """Install real generation composition in both ARQ roles that execute generation."""
    ctx.setdefault(
        "generation_job_service",
        build_complete_generation_service(artifact_store=get_runtime_artifact_store()),
    )


class IngestWorkerSettings:
    functions = [run_ingest]
    redis_settings = _REDIS_SETTINGS
    queue_name = REDIS_QUEUES["ingest"]
    job_timeout = _JOB_TIMEOUT
    max_jobs = settings.worker_ingest_max_jobs


class GenerationWorkerSettings:
    on_startup = configure_generation
    functions = [run_generation]
    redis_settings = _REDIS_SETTINGS
    queue_name = REDIS_QUEUES["generation"]
    job_timeout = _JOB_TIMEOUT
    max_jobs = settings.worker_generation_max_jobs


class AnalysisWorkerSettings:
    # Analysis outbox tasks are not implemented; GC is the one owned cron.
    functions = [gc_orphan_artifacts]
    cron_jobs = [cron(gc_orphan_artifacts, minute={0}, second={0})]
    redis_settings = _REDIS_SETTINGS
    queue_name = REDIS_QUEUES["analysis"]
    job_timeout = _JOB_TIMEOUT
    max_jobs = settings.worker_analysis_max_jobs


class ExportWorkerSettings:
    functions = [run_geojson_export, run_geopackage_export]
    redis_settings = _REDIS_SETTINGS
    queue_name = REDIS_QUEUES["export"]
    job_timeout = _JOB_TIMEOUT
    max_jobs = settings.worker_export_max_jobs


class LegacyWorkerSettings:
    on_startup = configure_generation
    # Drain already-queued legacy jobs, including GC, without a duplicate cron.
    functions = [
        run_generation, run_ingest, run_geojson_export,
        run_geopackage_export, gc_orphan_artifacts,
    ]
    redis_settings = _REDIS_SETTINGS
    queue_name = LEGACY_QUEUE
    job_timeout = _JOB_TIMEOUT
    max_jobs = 1


WorkerSettings = LegacyWorkerSettings
