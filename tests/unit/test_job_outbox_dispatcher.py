import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

from backend.app.models.job_outbox import JobOutbox
from backend.app.services.job_dispatcher import (
    MAX_DISPATCH_BATCH_SIZE,
    DispatchOutcome,
    JobQueueMessage,
    build_pending_outbox_query,
    dispatch_outbox_message,
)


class FakeRedisEnqueuer:
    def __init__(self, *, fail_calls: int = 0) -> None:
        self.fail_calls = fail_calls
        self.attempts = 0
        self.messages: list[JobQueueMessage] = []

    async def enqueue(self, message: JobQueueMessage) -> None:
        self.attempts += 1
        self.messages.append(message)
        if self.attempts <= self.fail_calls:
            raise ConnectionError("redis unavailable")


def _make_outbox() -> JobOutbox:
    return JobOutbox(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        queue_name="generation",
        payload={"task": "run_generation", "run_id": str(uuid.uuid4())},
        status="pending",
        delivery_attempts=0,
        last_error=None,
        last_attempt_at=None,
        next_attempt_at=None,
        dispatched_at=None,
    )


def test_outbox_persists_pending_enqueue_state() -> None:
    columns = JobOutbox.__table__.c
    for field_name in {
        "id",
        "job_id",
        "queue_name",
        "payload",
        "status",
        "delivery_attempts",
        "last_error",
        "last_attempt_at",
        "next_attempt_at",
        "dispatched_at",
        "created_at",
        "updated_at",
    }:
        assert field_name in columns

    unique_constraints = {
        constraint.name
        for constraint in JobOutbox.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_job_outbox_job_id" in unique_constraints

    constraint_names = {
        constraint.name
        for constraint in JobOutbox.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_job_outbox_queue_name",
        "ck_job_outbox_status",
        "ck_job_outbox_delivery_attempts_nonnegative",
        "ck_job_outbox_status_timestamp",
    } <= constraint_names


def test_outbox_references_job_and_uses_stable_enqueue_key() -> None:
    message = _make_outbox()
    job_fk = next(iter(JobOutbox.__table__.c.job_id.foreign_keys))
    assert job_fk.target_fullname == "jobs.id"
    assert job_fk.ondelete == "CASCADE"
    assert message.enqueue_key == f"job:{message.job_id}"


def test_pending_query_is_bounded_and_uses_skip_locked() -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    query = build_pending_outbox_query(at=now, limit=25)
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql

    with pytest.raises(ValueError, match="limit must be between"):
        build_pending_outbox_query(at=now, limit=0)
    with pytest.raises(ValueError, match="limit must be between"):
        build_pending_outbox_query(at=now, limit=MAX_DISPATCH_BATCH_SIZE + 1)


def test_successful_dispatch_is_repeat_safe() -> None:
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    message = _make_outbox()
    enqueuer = FakeRedisEnqueuer()

    first = asyncio.run(dispatch_outbox_message(message, enqueuer, now=now))
    second = asyncio.run(
        dispatch_outbox_message(message, enqueuer, now=now + timedelta(seconds=1))
    )

    assert first is DispatchOutcome.DISPATCHED
    assert second is DispatchOutcome.ALREADY_DISPATCHED
    assert enqueuer.attempts == 1
    assert message.status == "dispatched"
    assert message.delivery_attempts == 1
    assert message.last_attempt_at == now
    assert message.dispatched_at == now
    assert enqueuer.messages[0].enqueue_key == f"job:{message.job_id}"


def test_failed_delivery_stays_pending_and_can_be_retried() -> None:
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    retry_delay = timedelta(seconds=30)
    message = _make_outbox()
    enqueuer = FakeRedisEnqueuer(fail_calls=1)

    failed = asyncio.run(
        dispatch_outbox_message(
            message,
            enqueuer,
            now=now,
            retry_delay=retry_delay,
        )
    )
    too_early = asyncio.run(
        dispatch_outbox_message(message, enqueuer, now=now + timedelta(seconds=10))
    )
    retried = asyncio.run(
        dispatch_outbox_message(message, enqueuer, now=now + retry_delay)
    )

    assert failed is DispatchOutcome.RETRY_SCHEDULED
    assert too_early is DispatchOutcome.NOT_DUE
    assert retried is DispatchOutcome.DISPATCHED
    assert enqueuer.attempts == 2
    assert message.delivery_attempts == 2
    assert message.status == "dispatched"
    assert message.last_error is None
    assert message.next_attempt_at is None


def test_failure_record_requires_future_retry() -> None:
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    message = _make_outbox()
    with pytest.raises(ValueError, match="retry_at must be later"):
        message.record_delivery_failure(
            error="redis unavailable",
            attempted_at=now,
            retry_at=now,
        )
