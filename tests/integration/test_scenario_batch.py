"""PostgreSQL ScenarioBatch size, provenance, lifecycle and claim-capacity checks."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.application.generation import (
    GenerationClaimDisposition,
    GenerationRetryScheduled,
)
from backend.app.db.generation_state import SqlAlchemyGenerationStateStore
from backend.app.db.scenario_batch import (
    ScenarioBatchError,
    SqlAlchemyScenarioBatchStore,
)
from backend.app.db.session import engine
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.project import Project
from backend.app.models.scenario_batch import ScenarioBatch, ScenarioBatchRun
from core.urban_generator.domain import RunContext, StageResult, TerritorySnapshot
from core.urban_generator.stages import StageSkipReason

SessionFactory: Callable[[], Session] = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)


class SkippedFixtureStage:
    name = "fixture"
    version = "1"
    dependencies: tuple[str, ...] = ()

    def validate_input(self, value: object) -> object:
        return value

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: object,
        config: object,
    ) -> StageResult[object]:
        raise NotImplementedError


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


def _queued_children(
    *,
    count: int,
    project_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
    with SessionFactory() as session:
        if project_id is None:
            project = Project(
                name="ScenarioBatch acceptance",
                working_srid=32637,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            project_id = project.id
        ids: list[uuid.UUID] = []
        for index in range(count):
            run = GenerationRun(
                project_id=project_id,
                status="queued",
                mode="EXPANSION",
                seed=index + 17,
                working_srid=32637,
                config_json={"test": "batch"},
                config_schema_version="fixture-v1",
                commit_sha="a" * 40,
            )
            session.add(run)
            session.flush()
            session.add(
                Job(
                    project_id=project_id,
                    run_id=run.id,
                    job_type="generation_run",
                    idempotency_key=f"run:{run.id}",
                    status="queued",
                    attempt_count=0,
                    max_attempts=3,
                )
            )
            ids.append(run.id)
        session.commit()
        return project_id, tuple(ids)


def _finish_success(store: SqlAlchemyGenerationStateStore, run_id: uuid.UUID) -> None:
    store.skip_stage(
        run_id=run_id,
        stage=SkippedFixtureStage(),
        reason=StageSkipReason.REQUESTED,
        blocked_by=(),
    )
    store.complete_run(run_id=run_id, expected_stage_names=("fixture",))


@pytest.mark.parametrize("count", [3, 10])
def test_batch_seals_ordered_children_and_persists_bounds(count: int) -> None:
    project_id, run_ids = _queued_children(count=count)
    store = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    batch_id = store.create(
        project_id=project_id, run_ids=tuple(reversed(run_ids)), concurrency_limit=2
    )

    snapshot = store.refresh(batch_id=batch_id)
    assert snapshot.batch_id == batch_id
    assert snapshot.project_id == project_id
    assert snapshot.run_ids == tuple(reversed(run_ids))
    assert snapshot.concurrency_limit == 2
    assert snapshot.active_runs == 0
    assert snapshot.status == "queued"
    with SessionFactory() as session:
        batch = session.get(ScenarioBatch, batch_id)
        assert batch is not None and batch.status == "queued"
        assert [member.position for member in batch.members] == list(range(count))
        assert tuple(member.run_id for member in batch.members) == tuple(reversed(run_ids))


@pytest.mark.parametrize("count", [2, 11])
def test_rejects_batch_outside_three_to_ten_children(count: int) -> None:
    project_id, run_ids = _queued_children(count=count)
    store = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    with pytest.raises(ScenarioBatchError, match="3–10"):
        store.create(project_id=project_id, run_ids=run_ids, concurrency_limit=1)


def test_batch_rejects_duplicate_foreign_nonqueued_or_multiple_membership() -> None:
    project_id, ids = _queued_children(count=4)
    foreign_project, foreign_ids = _queued_children(count=1)
    store = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    with pytest.raises(ScenarioBatchError, match="distinct"):
        store.create(
            project_id=project_id, run_ids=(ids[0], ids[0], ids[1]), concurrency_limit=1
        )
    with pytest.raises(ScenarioBatchError, match="same project"):
        store.create(
            project_id=project_id,
            run_ids=(ids[0], ids[1], foreign_ids[0]),
            concurrency_limit=1,
        )
    with pytest.raises(ScenarioBatchError, match="concurrency_limit"):
        store.create(project_id=project_id, run_ids=ids[:3], concurrency_limit=4)
    with pytest.raises(ScenarioBatchError, match="concurrency_limit"):
        store.create(project_id=project_id, run_ids=ids[:3], concurrency_limit=True)
    with SessionFactory() as session:
        run = session.get(GenerationRun, ids[0])
        assert run is not None
        run.status = "running"
        session.commit()
    with pytest.raises(ScenarioBatchError, match="queued"):
        store.create(project_id=project_id, run_ids=ids[:3], concurrency_limit=1)
    with SessionFactory() as session:
        run = session.get(GenerationRun, ids[0])
        assert run is not None
        run.status = "queued"
        session.commit()
    store.create(project_id=project_id, run_ids=ids[:3], concurrency_limit=1)
    with pytest.raises(ScenarioBatchError, match="already belongs"):
        store.create(
            project_id=project_id,
            run_ids=(ids[0], ids[3], ids[1]),
            concurrency_limit=1,
        )
    assert foreign_project != project_id


def test_postgres_rejects_short_finalize_and_frozen_membership() -> None:
    project_id, ids = _queued_children(count=4)
    with SessionFactory() as session:
        batch = ScenarioBatch(
            id=uuid.uuid4(), project_id=project_id, status="draft", concurrency_limit=1
        )
        session.add(batch)
        session.flush()
        session.add_all(
            ScenarioBatchRun(batch_id=batch.id, run_id=run_id, position=position)
            for position, run_id in enumerate(ids[:2])
        )
        session.commit()
        batch_id = batch.id

    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE scenario_batches SET status='queued' WHERE id=:id"),
                {"id": batch_id},
            )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO scenario_batch_runs (batch_id, run_id, position) "
                "VALUES (:batch, :run, 2)"
            ),
            {"batch": batch_id, "run": ids[2]},
        )
        conn.execute(
            text("UPDATE scenario_batches SET status='queued' WHERE id=:id"),
            {"id": batch_id},
        )
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scenario_batch_runs (batch_id, run_id, position) "
                    "VALUES (:batch, :run, 3)"
                ),
                {"batch": batch_id, "run": ids[3]},
            )
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM scenario_batch_runs "
                    "WHERE batch_id=:batch AND run_id=:run"
                ),
                {"batch": batch_id, "run": ids[2]},
            )
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE scenario_batches SET concurrency_limit=2 WHERE id=:id"),
                {"id": batch_id},
            )


def test_postgres_rejects_foreign_child_even_when_bypassing_repository() -> None:
    project_id, ids = _queued_children(count=2)
    _, foreign = _queued_children(count=1)
    with SessionFactory() as session:
        batch = ScenarioBatch(
            id=uuid.uuid4(), project_id=project_id, status="draft", concurrency_limit=1
        )
        session.add(batch)
        session.commit()
        batch_id = batch.id
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scenario_batch_runs (batch_id, run_id, position) "
                    "VALUES (:batch, :run, 0)"
                ),
                {"batch": batch_id, "run": foreign[0]},
            )
    with SessionFactory() as session:
        assert session.scalar(
            select(ScenarioBatchRun).where(ScenarioBatchRun.batch_id == batch_id)
        ) is None
    assert ids and foreign


def test_running_child_claims_are_serialized_by_parent_capacity() -> None:
    project_id, ids = _queued_children(count=3)
    batches = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    batch_id = batches.create(project_id=project_id, run_ids=ids, concurrency_limit=1)
    state = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)

    assert state.claim(run_id=ids[0]) is GenerationClaimDisposition.STARTED
    with pytest.raises(GenerationRetryScheduled) as exc:
        state.claim(run_id=ids[1])
    assert exc.value.delay_seconds == 30
    with SessionFactory() as session:
        waiting = session.get(GenerationRun, ids[1])
        job = session.scalar(select(Job).where(Job.run_id == ids[1]))
        assert waiting is not None and waiting.status == "queued"
        assert job is not None and job.attempt_count == 0 and job.status == "queued"
    snapshot = batches.refresh(batch_id=batch_id)
    assert snapshot.status == "running" and snapshot.active_runs == 1

    _finish_success(state, ids[0])
    assert state.claim(run_id=ids[1]) is GenerationClaimDisposition.STARTED
    with pytest.raises(GenerationRetryScheduled):
        state.claim(run_id=ids[2])
    _finish_success(state, ids[1])
    assert state.claim(run_id=ids[2]) is GenerationClaimDisposition.STARTED
    _finish_success(state, ids[2])
    final = batches.refresh(batch_id=batch_id)
    assert final.status == "succeeded"
    assert final.active_runs == 0
    assert final.run_ids == ids
    with SessionFactory() as session:
        parent = session.get(ScenarioBatch, batch_id)
        assert parent is not None
        assert parent.started_at is not None and parent.finished_at is not None
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE scenario_batches SET status='running' WHERE id=:id"),
                {"id": batch_id},
            )


def test_refresh_aggregates_cancelled_children_without_marking_batch_succeeded() -> None:
    project_id, ids = _queued_children(count=3)
    batches = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    batch_id = batches.create(project_id=project_id, run_ids=ids, concurrency_limit=2)
    state = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    assert state.claim(run_id=ids[0]) is GenerationClaimDisposition.STARTED
    assert state.request_cancel(run_id=ids[1]) is True
    assert state.request_cancel(run_id=ids[2]) is True
    _finish_success(state, ids[0])
    snapshot = batches.refresh(batch_id=batch_id)
    assert snapshot.status == "cancelled"
    assert snapshot.active_runs == 0
    with pytest.raises(ScenarioBatchError, match="does not exist"):
        batches.refresh(batch_id=uuid.uuid4())
