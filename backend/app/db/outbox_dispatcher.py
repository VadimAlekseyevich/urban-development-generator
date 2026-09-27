from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.services.job_dispatcher import (
    MAX_DISPATCH_BATCH_SIZE,
    DispatchOutcome,
    RedisJobEnqueuer,
    build_pending_outbox_query,
    dispatch_outbox_message,
)

DEFAULT_DISPATCH_BATCH_SIZE = 25
DEFAULT_DISPATCH_POLL_SECONDS = 5.0
_MAX_RETRY_DELAY_SECONDS = 300


@dataclass(frozen=True, slots=True)
class DispatchBatchResult:
    """Committed results from one bounded due-outbox scan."""

    claimed: int
    dispatched: int
    retry_scheduled: int


class SqlAlchemyOutboxDispatcher:
    """Recover due pending outbox messages using a PostgreSQL transaction per batch.

    FOR UPDATE SKIP LOCKED reserves rows across competing dispatcher processes. The
    row lock stays held while enqueue is attempted; the enqueuer must bound its network
    wait. Redis acceptance followed by a DB crash leaves pending provenance and leads
    to repeat delivery with the same deterministic ARQ identity on restart.
    """

    def __init__(
        self,
        *,
        enqueuer: RedisJobEnqueuer,
        session_factory: Callable[[], Session] = SessionLocal,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._enqueuer = enqueuer
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def dispatch_due(
        self,
        *,
        limit: int = DEFAULT_DISPATCH_BATCH_SIZE,
    ) -> DispatchBatchResult:
        now = self._clock()
        query = build_pending_outbox_query(at=now, limit=limit)
        dispatched = 0
        retry_scheduled = 0
        with self._session_factory() as session:
            with session.begin():
                rows = session.scalars(query).all()
                for row in rows:
                    retry_seconds = min(
                        30 * 2 ** min(row.delivery_attempts, 4),
                        _MAX_RETRY_DELAY_SECONDS,
                    )
                    outcome = await dispatch_outbox_message(
                        row,
                        self._enqueuer,
                        now=now,
                        retry_delay=timedelta(seconds=retry_seconds),
                    )
                    if outcome is DispatchOutcome.DISPATCHED:
                        dispatched += 1
                    elif outcome is DispatchOutcome.RETRY_SCHEDULED:
                        retry_scheduled += 1
                return DispatchBatchResult(
                    claimed=len(rows),
                    dispatched=dispatched,
                    retry_scheduled=retry_scheduled,
                )

    async def run_forever(
        self,
        *,
        stop: asyncio.Event,
        limit: int = DEFAULT_DISPATCH_BATCH_SIZE,
        poll_seconds: float = DEFAULT_DISPATCH_POLL_SECONDS,
    ) -> None:
        """Poll on startup and after each batch; recover pending rows after restart."""

        if not 0 < poll_seconds <= 60:
            raise ValueError("poll_seconds must be between 0 and 60")
        if not 1 <= limit <= MAX_DISPATCH_BATCH_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_DISPATCH_BATCH_SIZE}")
        while not stop.is_set():
            batch = await self.dispatch_due(limit=limit)
            if batch.claimed == limit:
                # A full batch may mean more due work; bound each DB transaction
                # while draining a backlog without an unnecessary polling delay.
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
