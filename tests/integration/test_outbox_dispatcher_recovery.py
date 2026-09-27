"""PostgreSQL acceptance of the real restartable outbox claim/commit loop."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from backend.app.db.outbox_dispatcher import SqlAlchemyOutboxDispatcher
from backend.app.db.session import engine
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.services.job_dispatcher import JobQueueMessage

SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
NOW = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


class DuplicateAwareQueue:
    def __init__(self, *, fail: int = 0, crash: int = 0) -> None:
        self.fail = fail
        self.crash = crash
        self.attempts: list[JobQueueMessage] = []
        self.accepted: set[str] = set()

    async def enqueue(self, message: JobQueueMessage) -> None:
        self.attempts.append(message)
        if self.fail:
            self.fail -= 1
            raise ConnectionError("redis unavailable")
        self.accepted.add(message.enqueue_key)
        if self.crash:
            self.crash -= 1
            # Simulate process termination after Redis acceptance, before the
            # DB transaction commits; ordinary exceptions are retryable delivery.
            raise SimulatedDispatcherCrash()


class SimulatedDispatcherCrash(BaseException):
    pass


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _create_outbox(*, count: int = 1) -> list[tuple[uuid.UUID, uuid.UUID]]:
    with SessionFactory() as session:
        project = Project(
            name="Outbox recovery fixture",
            working_srid=32637,
            boundary_metadata={},
        )
        session.add(project)
        session.flush()
        rows: list[tuple[uuid.UUID, uuid.UUID]] = []
        for _ in range(count):
            job = Job(
                project_id=project.id,
                job_type="normalize_dataset",
                idempotency_key=f"fixture:{uuid.uuid4()}",
                status="queued",
            )
            session.add(job)
            session.flush()
            outbox = JobOutbox(
                job_id=job.id,
                queue_name="ingest",
                payload={
                    "task": "run_ingest",
                    "job_id": str(job.id),
                    "dataset_version_id": str(uuid.uuid4()),
                },
                status="pending",
            )
            session.add(outbox)
            session.flush()
            rows.append((outbox.id, job.id))
        session.commit()
        return rows


def _outbox(row_id: uuid.UUID) -> JobOutbox:
    with SessionFactory() as session:
        row = session.get(JobOutbox, row_id)
        assert row is not None
        session.expunge(row)
        return row


def _dispatcher(
    queue: DuplicateAwareQueue,
    clock: datetime = NOW,
) -> SqlAlchemyOutboxDispatcher:
    return SqlAlchemyOutboxDispatcher(
        enqueuer=queue,
        session_factory=SessionFactory,
        clock=lambda: clock,
    )


def test_unenqueued_rows_recover_after_dispatcher_restart() -> None:
    (outbox_id, job_id), = _create_outbox()
    queue = DuplicateAwareQueue(fail=1)
    first = asyncio.run(_dispatcher(queue).dispatch_due())
    row = _outbox(outbox_id)

    assert (first.claimed, first.dispatched, first.retry_scheduled) == (1, 0, 1)
    assert row.status == "pending"
    assert row.delivery_attempts == 1
    assert row.last_error == "ConnectionError: redis unavailable"
    assert row.next_attempt_at == NOW + timedelta(seconds=30)
    assert asyncio.run(_dispatcher(queue, NOW + timedelta(seconds=15)).dispatch_due()).claimed == 0

    # A fresh dispatcher instance recovers the row from Postgres, not in-memory state.
    second = asyncio.run(
        _dispatcher(queue, NOW + timedelta(seconds=30)).dispatch_due()
    )
    row = _outbox(outbox_id)
    assert (second.claimed, second.dispatched, second.retry_scheduled) == (1, 1, 0)
    assert row.status == "dispatched"
    assert row.dispatched_at == NOW + timedelta(seconds=30)
    assert row.delivery_attempts == 2
    assert row.next_attempt_at is None and row.last_error is None
    assert queue.accepted == {f"job:{job_id}"}
    assert all(message.enqueue_key == f"job:{job_id}" for message in queue.attempts)
    assert asyncio.run(_dispatcher(queue).dispatch_due()).claimed == 0


def test_skip_locked_claims_other_rows_without_double_dispatch() -> None:
    (first_id, first_job), (second_id, second_job) = _create_outbox(count=2)
    queue = DuplicateAwareQueue()
    with SessionFactory() as blocker:
        with blocker.begin():
            first = blocker.scalar(
                select(JobOutbox)
                .where(JobOutbox.id == first_id)
                .with_for_update()
            )
            assert first is not None
            batch = asyncio.run(_dispatcher(queue).dispatch_due(limit=2))
            assert (batch.claimed, batch.dispatched) == (1, 1)
            assert _outbox(first_id).status == "pending"
            assert _outbox(second_id).status == "dispatched"
            assert queue.accepted == {f"job:{second_job}"}

    followup = asyncio.run(_dispatcher(queue).dispatch_due(limit=2))
    assert (followup.claimed, followup.dispatched) == (1, 1)
    assert queue.accepted == {f"job:{first_job}", f"job:{second_job}"}
    assert len(queue.attempts) == 2


def test_redis_acceptance_then_db_crash_recovers_same_queue_identity() -> None:
    (outbox_id, job_id), = _create_outbox()
    queue = DuplicateAwareQueue(crash=1)
    with pytest.raises(SimulatedDispatcherCrash):
        asyncio.run(_dispatcher(queue).dispatch_due())
    # Neither the enqueue ack nor the delivery attempt was committed.
    row = _outbox(outbox_id)
    assert row.status == "pending" and row.delivery_attempts == 0
    assert queue.accepted == {f"job:{job_id}"}

    recovered = asyncio.run(_dispatcher(queue).dispatch_due())
    assert (recovered.claimed, recovered.dispatched) == (1, 1)
    assert _outbox(outbox_id).delivery_attempts == 1
    assert [msg.enqueue_key for msg in queue.attempts] == [
        f"job:{job_id}", f"job:{job_id}"
    ]
    assert len(queue.accepted) == 1


def test_dispatcher_polls_pending_rows_and_stops_cooperatively() -> None:
    (outbox_id, _), = _create_outbox()
    queue = DuplicateAwareQueue()
    async def exercise() -> None:
        stop = asyncio.Event()
        dispatcher = _dispatcher(queue)
        task = asyncio.create_task(
            dispatcher.run_forever(stop=stop, poll_seconds=0.01)
        )
        for _ in range(30):
            if _outbox(outbox_id).status == "dispatched":
                break
            await asyncio.sleep(0.01)
        assert _outbox(outbox_id).status == "dispatched"
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(exercise())
    assert len(queue.attempts) == 1
