from arq.connections import RedisSettings

from backend.app.core.config import settings
from worker.tasks import run_generation


class WorkerSettings:
    functions = [run_generation]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 2
    job_timeout = 60 * 60
