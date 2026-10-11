"""Real Redis queue isolation smoke; no worker executes queued tasks in this test."""

from __future__ import annotations

import asyncio
import uuid

from arq.connections import RedisSettings, create_pool

from backend.app.core.config import settings
from backend.app.services.job_dispatcher import JobQueueMessage
from worker.outbox_dispatcher import ArqJobEnqueuer
from worker.queues import REDIS_QUEUES


def test_real_redis_routes_outbox_tasks_to_separate_queues() -> None:
    async def exercise() -> None:
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        job_ids: list[str] = []
        try:
            enqueuer = ArqJobEnqueuer(redis)
            ingest_job, generation_job, version_id, run_id = (
                uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            )
            cases = [
                (
                    "ingest", ingest_job,
                    {"task": "run_ingest", "job_id": str(ingest_job),
                     "dataset_version_id": str(version_id)},
                ),
                (
                    "generation", generation_job,
                    {"task": "run_generation", "run_id": str(run_id)},
                ),
            ]
            for queue, job_id, payload in cases:
                key = f"job:{job_id}"
                job_ids.append(key)
                message = JobQueueMessage(
                    outbox_id=uuid.uuid4(),
                    job_id=job_id,
                    enqueue_key=key,
                    queue_name=queue,
                    payload=payload,
                )
                await enqueuer.enqueue(message)
                await enqueuer.enqueue(message)  # duplicate delivery stays idempotent
                assert await redis.zscore(REDIS_QUEUES[queue], key) is not None
                for other, queue_key in REDIS_QUEUES.items():
                    if other != queue:
                        assert await redis.zscore(queue_key, key) is None
        finally:
            for key in job_ids:
                for name in REDIS_QUEUES.values():
                    await redis.zrem(name, key)
                await redis.delete(f"arq:job:{key}", f"arq:result:{key}")
            await redis.aclose()

    asyncio.run(exercise())
