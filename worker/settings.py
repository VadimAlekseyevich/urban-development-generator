from arq import cron
from arq.connections import RedisSettings

from backend.app.core.config import settings
from worker.tasks import gc_orphan_artifacts, run_generation, run_geojson_export, run_ingest


class WorkerSettings:
    functions = [
        run_generation,
        run_ingest,
        run_geojson_export,
        cron(gc_orphan_artifacts, minute={0}, second={0}),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 2
    job_timeout = 60 * 60
