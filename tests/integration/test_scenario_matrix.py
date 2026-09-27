"""PostgreSQL matrix creation: one transaction for runs, jobs, outbox and sealed batch."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.application.generation import GenerationClaimDisposition, GenerationRetryScheduled
from backend.app.application.scenario_matrix import (
    ScenarioConfigVariant,
    ScenarioMatrixSpec,
    expand_scenario_matrix,
)
from backend.app.db.generation_state import SqlAlchemyGenerationStateStore
from backend.app.db.scenario_batch import ScenarioBatchError, SqlAlchemyScenarioBatchStore
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.models.scenario_batch import ScenarioBatch, ScenarioBatchRun
from core.urban_generator.domain import RunMode

WORKING_SRID = 32637
SessionFactory: Callable[[], Session] = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)


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


def _project(*, boundary: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    with SessionFactory() as session:
        project = Project(
            name="Scenario matrix integration",
            working_srid=WORKING_SRID,
            boundary_metadata={},
            boundary=(
                WKTElement(
                    "MULTIPOLYGON(((0 0, 100 0, 100 100, 0 100, 0 0)))",
                    srid=WORKING_SRID,
                )
                if boundary else None
            ),
        )
        session.add(project)
        session.flush()
        dataset = Dataset(project_id=project.id, kind="roads")
        session.add(dataset)
        session.flush()
        version = DatasetVersion(
            dataset_id=dataset.id,
            status="ready",
            version=1,
            checksum_sha256="b" * 64,
            source_metadata={"source": "matrix-fixture"},
        )
        session.add(version)
        session.commit()
        return project.id, version.id


def _spec(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    variants: tuple[ScenarioConfigVariant, ...] | None = None,
) -> ScenarioMatrixSpec:
    return ScenarioMatrixSpec(
        project_id=project_id,
        mode=RunMode.EXPANSION,
        seeds=(19, 7),
        variants=variants or (
            ScenarioConfigVariant.from_config(
                name="high", config_json={"density": 3, "geometry": {"a": 1, "b": 2}}
            ),
            ScenarioConfigVariant.from_config(
                name="low", config_json={"density": 1}
            ),
        ),
        dataset_version_ids=(version_id,),
        config_schema_version="matrix-fixture-v1",
        commit_sha="c" * 40,
        concurrency_limit=1,
    )


def _counts() -> tuple[int, int, int, int, int]:
    with SessionFactory() as session:
        models = (ScenarioBatch, ScenarioBatchRun, GenerationRun, Job, JobOutbox)
        return tuple(
            session.scalar(select(func.count()).select_from(model)) or 0
            for model in models
        )  # type: ignore[return-value]


def test_matrix_creates_reproducible_queued_children_jobs_and_outbox() -> None:
    project_id, version_id = _project()
    store = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    spec = _spec(project_id, version_id)
    expected = expand_scenario_matrix(spec)

    created = store.create_matrix(spec=spec)

    assert len(created.run_ids) == 4
    assert len(set(created.run_ids)) == 4
    snapshot = store.refresh(batch_id=created.batch_id)
    assert snapshot.run_ids == created.run_ids
    assert snapshot.project_id == project_id
    assert snapshot.status == "queued"
    assert snapshot.concurrency_limit == 1
    assert snapshot.active_runs == 0
    with SessionFactory() as session:
        batch = session.get(ScenarioBatch, created.batch_id)
        assert batch is not None and batch.status == "queued"
        members = session.scalars(
            select(ScenarioBatchRun)
            .where(ScenarioBatchRun.batch_id == created.batch_id)
            .order_by(ScenarioBatchRun.position)
        ).all()
        assert [(m.position, m.run_id) for m in members] == list(
            enumerate(created.run_ids)
        )
        for run_id, row in zip(created.run_ids, expected, strict=True):
            run = session.get(GenerationRun, run_id)
            job = session.scalar(select(Job).where(Job.run_id == run_id))
            assert run is not None and job is not None
            outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
            assert run.status == job.status == "queued"
            assert run.seed == row.seed
            assert run.config_json == row.config_json
            assert (run.mode, run.working_srid) == ("EXPANSION", WORKING_SRID)
            assert run.config_schema_version == spec.config_schema_version
            assert run.commit_sha == spec.commit_sha
            assert [v.id for v in run.dataset_versions] == [version_id]
            assert job.attempt_count == 0
            assert job.idempotency_key == f"run:{run_id}"
            assert outbox is not None
            assert outbox.status == "pending" and outbox.queue_name == "generation"
            assert outbox.payload == {
                "task": "run_generation", "run_id": str(run_id)
            }
            assert outbox.enqueue_key == f"job:{job.id}"

    # The existing admission protocol, not this matrix creator, is still authoritative.
    state = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    assert state.claim(run_id=created.run_ids[0]) is GenerationClaimDisposition.STARTED
    with pytest.raises(GenerationRetryScheduled):
        state.claim(run_id=created.run_ids[1])
    with SessionFactory() as session:
        waiting = session.get(GenerationRun, created.run_ids[1])
        job = session.scalar(select(Job).where(Job.run_id == created.run_ids[1]))
        assert waiting is not None and waiting.status == "queued"
        assert job is not None and job.attempt_count == 0


def test_same_matrix_spec_has_same_semantic_order_but_distinct_run_ownership() -> None:
    project_id, version_id = _project()
    store = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    first_spec = _spec(project_id, version_id)
    second_spec = _spec(
        project_id,
        version_id,
        variants=tuple(reversed(first_spec.variants)),
    )
    second_spec = ScenarioMatrixSpec(
        project_id=second_spec.project_id,
        mode=second_spec.mode,
        seeds=(7, 19),
        variants=second_spec.variants,
        dataset_version_ids=second_spec.dataset_version_ids,
        config_schema_version=second_spec.config_schema_version,
        commit_sha=second_spec.commit_sha,
        concurrency_limit=second_spec.concurrency_limit,
    )

    first = store.create_matrix(spec=first_spec)
    second = store.create_matrix(spec=second_spec)

    assert first.batch_id != second.batch_id
    assert set(first.run_ids).isdisjoint(second.run_ids)
    with SessionFactory() as session:
        def materialize(run_ids: tuple[uuid.UUID, ...]) -> list[tuple[int, dict[str, object]]]:
            return [
                (run.seed, run.config_json)
                for run_id in run_ids
                if (run := session.get(GenerationRun, run_id)) is not None
            ]

        assert materialize(first.run_ids) == materialize(second.run_ids)
        assert len(materialize(first.run_ids)) == 4
        assert _counts() == (2, 8, 8, 8, 8)


@pytest.mark.parametrize(
    "failure", ("missing_project", "missing_version", "foreign", "unready", "boundary")
)
def test_failed_matrix_preflight_rolls_back_all_children_and_outbox(failure: str) -> None:
    project_id, version_id = _project(boundary=failure != "boundary")
    foreign_project_id, foreign_version_id = _project()
    assert foreign_project_id != project_id
    target_project = uuid.uuid4() if failure == "missing_project" else project_id
    target_version = (
        uuid.uuid4() if failure == "missing_version"
        else foreign_version_id if failure == "foreign"
        else version_id
    )
    if failure == "unready":
        with SessionFactory() as session:
            version = session.get(DatasetVersion, version_id)
            assert version is not None
            version.status = "processing"
            session.commit()
    before = _counts()
    spec = _spec(target_project, target_version)
    store = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    with pytest.raises(ScenarioBatchError):
        store.create_matrix(spec=spec)
    assert _counts() == before


def test_rollback_if_database_rejects_batch_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, version_id = _project()
    spec = _spec(project_id, version_id)
    store = SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    before = _counts()

    # Prove transactional ownership even when final batch creation fails *after*
    # all the child runs/jobs/outbox rows were staged/flushed.
    def reject_seal(*_args: object, **_kwargs: object) -> uuid.UUID:
        raise ScenarioBatchError("injected seal failure")

    monkeypatch.setattr(store, "_seal", reject_seal)
    with pytest.raises(ScenarioBatchError, match="injected seal failure"):
        store.create_matrix(spec=spec)
    assert _counts() == before
