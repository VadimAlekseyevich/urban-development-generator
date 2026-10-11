"""Isolated ARQ workers: one queue, allowlisted functions, bounded concurrency.

LegacyWorkerSettings drains existing arq:queue jobs from previous deployments.
No new outbox message is dispatched to the legacy queue after S14-T01.
"""

from arq import cron
from arq.connections import RedisSettings

from backend.app.core.config import settings
from worker.queues import LEGACY_QUEUE, REDIS_QUEUES
from worker.tasks import (
    gc_orphan_artifacts,
    run_generation,
    run_geojson_export,
    run_geopackage_export,
    run_ingest,
)


class BaseWorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 60 * 60


class IngestWorkerSettings(BaseWorkerSettings):
    functions = [run_ingest]
    queue_name = REDIS_QUEUES["ingest"]
    max_jobs = settings.worker_ingest_max_jobs


class GenerationWorkerSettings(BaseWorkerSettings):
    functions = [run_generation]
    queue_name = REDIS_QUEUES["generation"]
    max_jobs = settings.worker_generation_max_jobs


class AnalysisWorkerSettings(BaseWorkerSettings):
    # No analysis outbox task exists yet. Run one bounded hourly maintenance
    # task here rather than scheduling GC on every isolated worker.
    functions = [cron(gc_orphan_artifacts, minute={0}, second={0})]
    queue_name = REDIS_QUEUES["analysis"]
    max_jobs = settings.worker_analysis_max_jobs


class ExportWorkerSettings(BaseWorkerSettings):
    functions = [run_geojson_export, run_geopackage_export]
    queue_name = REDIS_QUEUES["export"]
    max_jobs = settings.worker_export_max_jobs


class LegacyWorkerSettings(BaseWorkerSettings):
    """Consume existing arq:queue messages during rollout; never enqueue new ones."""

    # Registered for old queued maintenance messages, without adding another cron.
    functions = [run_generation, run_ingest, run_geojson_export, run_geopackage_export,
                 gc_orphan_artifacts]
    queue_name = LEGACY_QUEUE
    max_jobs = 1


# Compatibility for existing manual `arq worker.settings.WorkerSettings` invocations.
WorkerSettings = LegacyWorkerSettings
