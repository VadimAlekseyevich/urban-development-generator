from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from backend.app.application.checkpoints import (
    CheckpointIdentity,
    build_config_hash,
    build_resolved_input_hash,
)
from backend.app.application.generation_execution import GenerationRunStartDisposition
from backend.app.db.generation_execution_repository import (
    SqlAlchemyGenerationProgressRepository,
)
from backend.app.db.session import engine
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import (
    StageDiagnostic,
    StageResult,
    build_stage_fingerprint,
)
from core.urban_generator.domain.errors import PermanentError
from core.urban_generator.stages import StageSkipReason

WORKING_SRID = 32637
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _migrate_to_head() -> None:
    command.upgrade(Config("alembic.ini"), "head")


def _truncate() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None):
    _truncate()
    yield
    _truncate()


def _create_run() -> uuid.UUID:
    with SessionFactory() as session:
        project = Project(
            name="Generation progress",
            working_srid=WORKING_SRID,
            boundary_metadata={},
        )
        session.add(project)
        session.flush()
        run = GenerationRun(
            project_id=project.id,
            status="queued",
            mode="EXPANSION",
            seed=2026,
            working_srid=WORKING_SRID,
            config_json={},
            config_schema_version="test-v1",
        )
        session.add(run)
        session.commit()
        return run.id


def _identity(stage_name: str) -> CheckpointIdentity:
    return CheckpointIdentity(
        stage_name=stage_name,
        stage_version="1.0.0",
        input_hash=build_resolved_input_hash(
            input_parts=(f"{stage_name}:input",),
            expected_dependencies=(),
            dependency_outputs=(),
        ),
        config_hash=build_config_hash(f"{stage_name}:config"),
    )


def test_progress_repository_persists_run_and_stage_success() -> None:
    run_id = _create_run()
    start = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)
    ticks = iter(
        (
            start,
            start + timedelta(seconds=1),
            start + timedelta(seconds=2),
            start + timedelta(seconds=3),
        )
    )
    repository = SqlAlchemyGenerationProgressRepository(
        session_factory=SessionFactory,
        clock=lambda: next(ticks),
    )
    identity = _identity("roads")
    result = StageResult(
        output="roads-output",
        fingerprint=build_stage_fingerprint("roads-output"),
        diagnostics=(
            StageDiagnostic(
                code="roads.completed",
                message="roads completed",
            ),
        ),
    )

    assert repository.begin_run(run_id) is GenerationRunStartDisposition.STARTED
    repository.mark_stage_running(run_id=run_id, identity=identity)
    repository.mark_stage_succeeded(
        run_id=run_id,
        identity=identity,
        result=result,
    )
    repository.mark_run_succeeded(
        run_id=run_id,
        commit_sha="a" * 40,
    )

    with SessionFactory() as session:
        run = session.get(GenerationRun, run_id)
        stage = session.scalar(
            select(RunStageResult).where(
                RunStageResult.run_id == run_id,
                RunStageResult.stage_name == "roads",
            )
        )

        assert run is not None
        assert run.status == "succeeded"
        assert run.commit_sha == "a" * 40
        assert run.started_at == start
        assert run.finished_at == start + timedelta(seconds=3)

        assert stage is not None
        assert stage.status == "succeeded"
        assert stage.progress_percent == 100
        assert stage.input_hash == identity.input_hash
        assert stage.config_hash == identity.config_hash
        assert stage.output_fingerprint == result.fingerprint.value
        assert stage.diagnostics_json == [
            {
                "code": "roads.completed",
                "message": "roads completed",
                "level": "INFO",
            }
        ]
        assert stage.started_at == start + timedelta(seconds=1)
        assert stage.finished_at == start + timedelta(seconds=2)


def test_progress_repository_persists_skip_as_terminal_non_checkpoint_row() -> None:
    run_id = _create_run()
    repository = SqlAlchemyGenerationProgressRepository(
        session_factory=SessionFactory,
    )

    repository.begin_run(run_id)
    repository.mark_stage_skipped(
        run_id=run_id,
        stage_name="metrics",
        stage_version="1.0.0",
        reason=StageSkipReason.DEPENDENCY_SKIPPED,
        blocked_by=("final_validation",),
    )
    repository.mark_run_succeeded(
        run_id=run_id,
        commit_sha="b" * 40,
    )

    with SessionFactory() as session:
        stage = session.scalar(
            select(RunStageResult).where(
                RunStageResult.run_id == run_id,
                RunStageResult.stage_name == "metrics",
            )
        )
        assert stage is not None
        assert stage.status == "skipped"
        assert stage.progress_percent == 100
        assert stage.output_fingerprint is None
        assert stage.input_hash.startswith("sha256:")
        assert stage.config_hash.startswith("sha256:")
        assert stage.diagnostics_json[0]["code"] == "stage.skipped"


def test_progress_repository_persists_stage_and_run_failure() -> None:
    run_id = _create_run()
    repository = SqlAlchemyGenerationProgressRepository(
        session_factory=SessionFactory,
    )
    identity = _identity("buildings")

    repository.begin_run(run_id)
    repository.mark_stage_running(run_id=run_id, identity=identity)
    error = ValueError("building execution failed")
    repository.mark_stage_failed(
        run_id=run_id,
        stage_name="buildings",
        stage_version="1.0.0",
        error=error,
    )
    repository.mark_run_failed(run_id=run_id, error=error)

    with SessionFactory() as session:
        run = session.get(GenerationRun, run_id)
        stage = session.scalar(
            select(RunStageResult).where(
                RunStageResult.run_id == run_id,
                RunStageResult.stage_name == "buildings",
            )
        )
        assert run is not None and run.status == "failed"
        assert run.error_json is not None
        assert run.error_json["code"] == "generation.execution_failed"
        assert stage is not None and stage.status == "failed"
        assert stage.output_fingerprint is None
        assert stage.diagnostics_json[0]["code"] == "stage.execution_failed"


def test_progress_repository_does_not_reclaim_nonqueued_run_before_retry_policy() -> None:
    run_id = _create_run()
    repository = SqlAlchemyGenerationProgressRepository(
        session_factory=SessionFactory,
    )
    repository.begin_run(run_id)

    with pytest.raises(PermanentError, match="not claimable"):
        repository.begin_run(run_id)
