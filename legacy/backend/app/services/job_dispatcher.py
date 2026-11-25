import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sqlalchemy import Select, or_, select

from backend.app.models.job_outbox import JobOutbox

MAX_DISPATCH_BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class JobQueueMessage:
    """Queue-neutral payload that a Redis/ARQ adapter can enqueue."""

    outbox_id: uuid.UUID
    job_id: uuid.UUID
    enqueue_key: str
    queue_name: str
    payload: dict[str, object]


class DispatchOutcome(StrEnum):
    DISPATCHED = "dispatched"
    ALREADY_DISPATCHED = "already_dispatched"
    NOT_DUE = "not_due"
    RETRY_SCHEDULED = "retry_scheduled"


@runtime_checkable
class RedisJobEnqueuer(Protocol):
    """Port implemented by the Redis/ARQ adapter; DB remains authoritative."""

    async def enqueue(self, message: JobQueueMessage) -> None:
        """Enqueue one message using message.enqueue_key as deterministic queue identity."""


def build_pending_outbox_query(
    *,
    at: datetime,
    limit: int,
) -> Select[tuple[JobOutbox]]:
    """Build a bounded concurrent-safe claim query for due pending messages."""

    if limit < 1 or limit > MAX_DISPATCH_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_DISPATCH_BATCH_SIZE}")
    return (
        select(JobOutbox)
        .where(
            JobOutbox.status == "pending",
            or_(JobOutbox.next_attempt_at.is_(None), JobOutbox.next_attempt_at <= at),
        )
        .order_by(JobOutbox.created_at, JobOutbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


async def dispatch_outbox_message(
    message: JobOutbox,
    enqueuer: RedisJobEnqueuer,
    *,
    now: datetime,
    retry_delay: timedelta = timedelta(seconds=30),
) -> DispatchOutcome:
    """Attempt one enqueue without committing the surrounding DB transaction.

    Delivery is intentionally at-least-once. The caller persists the state change in the same
    DB transaction it owns. A process crash after Redis accepted the deterministic enqueue key
    but before the DB commit leaves this row pending, so the next dispatcher pass may repeat the
    same delivery instead of losing the job.
    """

    if retry_delay <= timedelta(0):
        raise ValueError("retry_delay must be positive")
    if message.status == "dispatched":
        return DispatchOutcome.ALREADY_DISPATCHED
    if not message.is_due(at=now):
        return DispatchOutcome.NOT_DUE

    envelope = JobQueueMessage(
        outbox_id=message.id,
        job_id=message.job_id,
        enqueue_key=message.enqueue_key,
        queue_name=message.queue_name,
        payload=dict(message.payload),
    )
    try:
        await enqueuer.enqueue(envelope)
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}".strip()
        message.record_delivery_failure(
            error=error_text,
            attempted_at=now,
            retry_at=now + retry_delay,
        )
        return DispatchOutcome.RETRY_SCHEDULED

    message.mark_dispatched(at=now)
    return DispatchOutcome.DISPATCHED
