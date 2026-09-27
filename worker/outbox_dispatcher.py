"""Dedicated restartable outbox-to-ARQ delivery process.

Run with: uv run python -m worker.outbox_dispatcher
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Protocol

from arq.connections import RedisSettings, create_pool

from backend.app.core.config import settings
from backend.app.db.outbox_dispatcher import SqlAlchemyOutboxDispatcher
from backend.app.services.job_dispatcher import JobQueueMessage

_ARQ_QUEUE_NAME = "arq:queue"
_ENQUEUE_TIMEOUT_SECONDS = 5.0
_SUPPORTED_LOGICAL_QUEUES = frozenset({"default", "ingest", "generation"})


class ArqQueueClient(Protocol):
    async def enqueue_job(
        self,
        function: str,
        *args: str,
        _job_id: str,
        _queue_name: str,
    ) -> object: ...


class ArqJobEnqueuer:
    """Translate allowlisted outbox payloads into stable ARQ task identities."""

    def __init__(self, redis: ArqQueueClient) -> None:
        self._redis = redis

    async def enqueue(self, message: JobQueueMessage) -> None:
        if message.queue_name not in _SUPPORTED_LOGICAL_QUEUES:
            raise ValueError("unsupported logical outbox queue")
        task = message.payload.get("task")
        if task == "run_generation":
            if set(message.payload) != {"task", "run_id"}:
                raise ValueError("invalid generation outbox payload")
            run_id = _uuid_str(message.payload["run_id"], field="run_id")
            args = (run_id,)
        elif task == "run_ingest":
            if set(message.payload) != {"task", "job_id", "dataset_version_id"}:
                raise ValueError("invalid ingest outbox payload")
            job_id = _uuid_str(message.payload["job_id"], field="job_id")
            if job_id != str(message.job_id):
                raise ValueError("ingest outbox job_id does not match authoritative job")
            version_id = _uuid_str(
                message.payload["dataset_version_id"], field="dataset_version_id"
            )
            args = (job_id, version_id)
        else:
            raise ValueError("unsupported outbox task")

        # ARQ returns None for a duplicate _job_id. This is an accepted enqueue:
        # after a crash between Redis acceptance and DB commit, the same stable
        # key must be acknowledged, not rejected or replaced with a fresh key.
        async with asyncio.timeout(_ENQUEUE_TIMEOUT_SECONDS):
            await self._redis.enqueue_job(
                task,
                *args,
                _job_id=message.enqueue_key,
                _queue_name=_ARQ_QUEUE_NAME,
            )


def _uuid_str(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a UUID string")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID string") from exc


async def main() -> None:
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await SqlAlchemyOutboxDispatcher(
            enqueuer=ArqJobEnqueuer(redis),
        ).run_forever(stop=asyncio.Event())
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
