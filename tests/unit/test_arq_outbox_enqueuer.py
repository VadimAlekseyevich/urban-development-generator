from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.app.services.job_dispatcher import JobQueueMessage
from worker.outbox_dispatcher import ArqJobEnqueuer


class FakeArq:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], str, str]] = []
        self.job_ids: set[str] = set()

    async def enqueue_job(
        self,
        function: str,
        *args: str,
        _job_id: str,
        _queue_name: str,
    ) -> object:
        self.calls.append((function, args, _job_id, _queue_name))
        if _job_id in self.job_ids:
            return None
        self.job_ids.add(_job_id)
        return _job_id


def _message(
    payload: dict[str, object],
    *,
    queue_name: str = "default",
    job_id: uuid.UUID | None = None,
) -> JobQueueMessage:
    job = job_id or uuid.uuid4()
    return JobQueueMessage(
        outbox_id=uuid.uuid4(),
        job_id=job,
        enqueue_key=f"job:{job}",
        queue_name=queue_name,
        payload=payload,
    )


def test_arq_enqueuer_routes_generation_and_accepts_duplicate_id() -> None:
    redis = FakeArq()
    run_id = uuid.uuid4()
    message = _message(
        {"task": "run_generation", "run_id": str(run_id)},
        queue_name="generation",
    )
    enqueuer = ArqJobEnqueuer(redis)

    asyncio.run(enqueuer.enqueue(message))
    asyncio.run(enqueuer.enqueue(message))

    assert redis.calls == [
        ("run_generation", (str(run_id),), message.enqueue_key, "arq:queue"),
        ("run_generation", (str(run_id),), message.enqueue_key, "arq:queue"),
    ]
    assert redis.job_ids == {message.enqueue_key}


def test_arq_enqueuer_routes_ingest_with_matching_authoritative_job_id() -> None:
    redis = FakeArq()
    job_id, version_id = uuid.uuid4(), uuid.uuid4()
    message = _message(
        {
            "task": "run_ingest",
            "job_id": str(job_id),
            "dataset_version_id": str(version_id),
        },
        queue_name="ingest",
        job_id=job_id,
    )
    asyncio.run(ArqJobEnqueuer(redis).enqueue(message))
    assert redis.calls == [
        (
            "run_ingest",
            (str(job_id), str(version_id)),
            message.enqueue_key,
            "arq:queue",
        ),
    ]


def test_arq_enqueuer_routes_geojson_export_with_authoritative_job_id() -> None:
    redis = FakeArq()
    job_id = uuid.uuid4()
    message = _message(
        {"task": "run_geojson_export", "job_id": str(job_id)},
        queue_name="export",
        job_id=job_id,
    )
    asyncio.run(ArqJobEnqueuer(redis).enqueue(message))
    assert redis.calls == [
        (
            "run_geojson_export",
            (str(job_id),),
            message.enqueue_key,
            "arq:queue",
        ),
    ]


@pytest.mark.parametrize(
    "payload, queue_name",
    [
        ({"task": "invalid", "run_id": str(uuid.uuid4())}, "default"),
        ({"task": "run_generation", "run_id": "not-a-uuid"}, "default"),
        ({"task": "run_generation", "run_id": 7}, "default"),
        ({"task": "run_generation", "run_id": str(uuid.uuid4()), "unexpected": 1}, "default"),
        ({"task": "run_generation", "run_id": str(uuid.uuid4())}, "unconfigured"),
        (
            {
                "task": "run_ingest",
                "job_id": str(uuid.uuid4()),
                "dataset_version_id": str(uuid.uuid4()),
            },
            "default",
        ),
        (
            {"task": "run_geojson_export", "job_id": str(uuid.uuid4())},
            "export",
        ),
        (
            {"task": "run_geojson_export", "job_id": "not-a-uuid"},
            "export",
        ),
    ],
)
def test_arq_enqueuer_rejects_unknown_or_mismatched_payload(
    payload: dict[str, object],
    queue_name: str,
) -> None:
    redis = FakeArq()
    with pytest.raises(ValueError):
        asyncio.run(
            ArqJobEnqueuer(redis).enqueue(_message(payload, queue_name=queue_name))
        )
    assert not redis.calls
