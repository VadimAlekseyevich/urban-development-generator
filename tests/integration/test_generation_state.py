from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.application.generation import (
    GenerationClaimDisposition,
    GenerationExecutionError,
    StageInvocation,
)
from backend.app.db.generation_state import SqlAlchemyGenerationStateStore
from backend.app.db.session import engine
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import (
    RunContext,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
)
from core.urban_generator.stages import StageSkipReason

WORKING_SRID = 32637
SessionFactory: Callable[[], Session] = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)


class DummyStage:
    name = "root"
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


def _setup(*, commit_sha: str | None = "a" * 40) -> tuple[uuid.UUID, uuid.UUID]:
    with SessionFactory() as session:
        project = Project(
            name="Generation state integration",
            working_srid=WORKING_SRID,
            boundary_metadata={},
        )
        session.add(project)
        session.flush()
        run = GenerationRun(
            project_id=project.id,
            mode="EXPANSION",
            status="queued",
            seed=17,
            working_srid=WORKING_SRID,
            config_json={"scenario": "fixture"},
            config_schema_version="test-v1",
            commit_sha=commit_sha,
        )
        session.add(run)
        session.flush()
        job = Job(
            project_id=project.id,
            run_id=run.id,
            job_type="generation_run",
            idempotency_key=f"run:{run.id}",
            status="queued",
            attempt_count=0,
            max_attempts=3,
        )
        session.add(job)
        session.commit()
        return run.id, job.id


def _identity(
    store: SqlAlchemyGenerationStateStore,
    run_id: uuid.UUID,
):
    return store.resolve_identity(
        run_id=run_id,
        stage=DummyStage(),
        invocation=StageInvocation(
            stage_input=1,
            input_parts=("fixture:input:v1",),
            config_parts=("fixture:config:v1",),
        ),
    )


def _rows(run_id: uuid.UUID, job_id: uuid.UUID):
    with SessionFactory() as session:
        run = session.get(GenerationRun, run_id)
        job = session.get(Job, job_id)
        stage = session.query(RunStageResult).filter_by(
            run_id=run_id, stage_name="root"
        ).one_or_none()
        assert run is not None and job is not None
        session.expunge(run)
        session.expunge(job)
        if stage is not None:
            session.expunge(stage)
        return run, job, stage


def test_progress_is_committed_stage_by_stage_before_run_success() -> None:
    run_id, job_id = _setup()
    store = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    assert store.claim(run_id=run_id) is GenerationClaimDisposition.STARTED

    run, job, stage = _rows(run_id, job_id)
    assert (run.status, job.status, job.attempt_count) == ("running", "running", 1)
    assert stage is None

    identity = _identity(store, run_id)
    store.start_stage(run_id=run_id, identity=identity)
    _, _, stage = _rows(run_id, job_id)
    assert stage is not None
    assert (stage.status, stage.progress_percent) == ("running", 0)
    assert stage.input_hash == identity.input_hash
    assert stage.config_hash == identity.config_hash
    assert stage.output_fingerprint is None

    result = StageResult(
        output=2,
        fingerprint=build_stage_fingerprint("root", "result:v1"),
    )
    store.complete_stage(run_id=run_id, stage_name="root", result=result)
    run, job, stage = _rows(run_id, job_id)
    assert stage is not None
    assert (run.status, job.status, stage.status) == (
        "running", "running", "succeeded"
    )
    assert stage.progress_percent == 100
    assert stage.output_fingerprint == result.fingerprint.value

    store.complete_run(run_id=run_id, expected_stage_names=("root",))
    run, job, _ = _rows(run_id, job_id)
    assert run.status == job.status == "succeeded"
    assert run.finished_at is not None
    assert job.finished_at is not None
    assert store.claim(run_id=run_id) is GenerationClaimDisposition.ALREADY_SUCCEEDED


def test_failed_stage_and_run_are_persisted_without_false_success() -> None:
    run_id, job_id = _setup()
    store = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    store.claim(run_id=run_id)
    store.start_stage(run_id=run_id, identity=_identity(store, run_id))
    store.fail_stage(run_id=run_id, stage_name="root")
    store.fail_run(run_id=run_id, stage_name="root")

    run, job, stage = _rows(run_id, job_id)
    assert stage is not None and stage.status == "failed"
    assert stage.output_fingerprint is None
    assert run.status == job.status == "failed"
    assert run.error_json is not None
    assert run.error_json["details"]["stage_name"] == "root"
    assert job.error_class == "permanent"
    assert job.attempt_count == 1

    with pytest.raises(GenerationExecutionError, match="queued"):
        store.claim(run_id=run_id)


def test_skipped_stage_has_no_output_fingerprint_and_can_complete_run() -> None:
    run_id, job_id = _setup()
    store = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    store.claim(run_id=run_id)
    store.skip_stage(
        run_id=run_id,
        stage=DummyStage(),
        reason=StageSkipReason.REQUESTED,
        blocked_by=(),
    )
    _, _, stage = _rows(run_id, job_id)
    assert stage is not None
    assert stage.status == "skipped"
    assert stage.output_fingerprint is None
    assert stage.progress_percent == 100
    assert stage.diagnostics_json[0]["message"] == "REQUESTED"

    store.complete_run(run_id=run_id, expected_stage_names=("root",))
    run, job, _ = _rows(run_id, job_id)
    assert run.status == job.status == "succeeded"


def test_generation_refuses_run_without_real_commit_provenance() -> None:
    run_id, job_id = _setup(commit_sha=None)
    store = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)

    with pytest.raises(GenerationExecutionError, match="commit SHA"):
        store.claim(run_id=run_id)
    run, job, stage = _rows(run_id, job_id)
    assert run.status == job.status == "queued"
    assert job.attempt_count == 0
    assert stage is None


def test_generation_rejects_incomplete_stage_progress_on_finalize() -> None:
    run_id, job_id = _setup()
    store = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    store.claim(run_id=run_id)
    store.start_stage(run_id=run_id, identity=_identity(store, run_id))

    with pytest.raises(GenerationExecutionError, match="incomplete stage results"):
        store.complete_run(run_id=run_id, expected_stage_names=("root",))
    run, job, stage = _rows(run_id, job_id)
    assert run.status == job.status == "running"
    assert stage is not None and stage.status == "running"


def test_checkpoint_metadata_hit_is_not_treated_as_typed_output_rehydration() -> None:
    run_id, _job_id = _setup()
    store = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    store.claim(run_id=run_id)
    identity = _identity(store, run_id)
    store.start_stage(run_id=run_id, identity=identity)
    store.complete_stage(
        run_id=run_id,
        stage_name="root",
        result=StageResult(
            output=2,
            fingerprint=build_stage_fingerprint("root", "result:v1"),
        ),
    )

    with pytest.raises(GenerationExecutionError, match="typed output hydration"):
        _identity(store, run_id)
