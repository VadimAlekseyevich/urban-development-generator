from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.adapters.checkpoints import (
    PersistedCheckpointError,
    SqlAlchemyCheckpointStore,
)
from backend.app.application.checkpoints import (
    CheckpointIdentity,
    DependencyOutputFingerprint,
    build_config_hash,
    build_resolved_input_hash,
)
from backend.app.db.session import engine
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import build_stage_fingerprint

WORKING_SRID = 32637


def _migrate_to_head() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")


def _truncate_state() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    _truncate_state()
    yield
    _truncate_state()


def _project(session: Session) -> Project:
    project = Project(
        name="Checkpoint integration",
        working_srid=WORKING_SRID,
        boundary_metadata={},
    )
    session.add(project)
    session.flush()
    return project


def _run(session: Session, project: Project) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        status="running",
        mode="EXPANSION",
        seed=2026,
        working_srid=WORKING_SRID,
        config_json={},
        config_schema_version="test-v1",
    )
    session.add(run)
    session.flush()
    return run


def _stage_result(
    session: Session,
    run: GenerationRun,
    *,
    stage_name: str,
    stage_version: str = "1.0.0",
    status: str = "succeeded",
    input_hash: str | None = None,
    config_hash: str | None = None,
    output_payload: str | None = None,
) -> RunStageResult:
    output_fingerprint = (
        build_stage_fingerprint(output_payload).value
        if output_payload is not None
        else None
    )
    result = RunStageResult(
        run_id=run.id,
        stage_name=stage_name,
        stage_version=stage_version,
        status=status,
        progress_percent=100 if status == "succeeded" else 0,
        input_hash=input_hash or f"sha256:{'a' * 64}",
        config_hash=config_hash or f"sha256:{'b' * 64}",
        output_fingerprint=output_fingerprint,
        diagnostics_json=[],
        artifact_refs_json=[],
    )
    session.add(result)
    session.flush()
    return result


def _dependency_outputs() -> tuple[DependencyOutputFingerprint, ...]:
    return (
        DependencyOutputFingerprint(
            stage_name="demography",
            output_fingerprint=build_stage_fingerprint("demography-output"),
        ),
        DependencyOutputFingerprint(
            stage_name="roads",
            output_fingerprint=build_stage_fingerprint("roads-output"),
        ),
    )


def _expected_identity() -> CheckpointIdentity:
    return CheckpointIdentity(
        stage_name="infrastructure",
        stage_version="1.0.0",
        input_hash=build_resolved_input_hash(
            input_parts=("snapshot:v1",),
            expected_dependencies=("demography", "roads"),
            dependency_outputs=_dependency_outputs(),
        ),
        config_hash=build_config_hash("infrastructure-config:v1"),
    )


def _persist_dependencies(
    session: Session,
    run: GenerationRun,
    *,
    roads_status: str = "succeeded",
    roads_output: str | None = "roads-output",
) -> None:
    _stage_result(
        session,
        run,
        stage_name="roads",
        status=roads_status,
        output_payload=roads_output,
    )
    _stage_result(
        session,
        run,
        stage_name="demography",
        output_payload="demography-output",
    )


def test_store_reuses_exact_successful_same_run_checkpoint() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        run = _run(session, project)
        _persist_dependencies(session, run)
        expected = _expected_identity()
        candidate = _stage_result(
            session,
            run,
            stage_name="infrastructure",
            stage_version=expected.stage_version,
            input_hash=expected.input_hash,
            config_hash=expected.config_hash,
            output_payload="infrastructure-output",
        )
        session.commit()

        resolution = SqlAlchemyCheckpointStore(session=session).resolve(
            run_id=run.id,
            stage_name="infrastructure",
            stage_version="1.0.0",
            input_parts=("snapshot:v1",),
            config_parts=("infrastructure-config:v1",),
            expected_dependencies=("demography", "roads"),
        )

        assert resolution.identity == expected
        assert tuple(
            item.stage_name for item in resolution.dependency_outputs
        ) == ("demography", "roads")
        assert resolution.reusable is not None
        assert resolution.reusable.stage_result_id == candidate.id
        assert resolution.reusable.run_id == run.id
        assert resolution.reusable.identity == expected
        assert resolution.reusable.output_fingerprint == (
            build_stage_fingerprint("infrastructure-output")
        )


@pytest.mark.parametrize(
    ("stage_version", "input_hash", "config_hash"),
    [
        ("2.0.0", None, None),
        ("1.0.0", f"sha256:{'c' * 64}", None),
        ("1.0.0", None, f"sha256:{'d' * 64}"),
    ],
)
def test_store_rejects_stale_checkpoint_identity_as_cache_miss(
    stage_version: str,
    input_hash: str | None,
    config_hash: str | None,
) -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        run = _run(session, project)
        _persist_dependencies(session, run)
        expected = _expected_identity()
        _stage_result(
            session,
            run,
            stage_name="infrastructure",
            stage_version=stage_version,
            input_hash=input_hash or expected.input_hash,
            config_hash=config_hash or expected.config_hash,
            output_payload="stale-output",
        )
        session.commit()

        resolution = SqlAlchemyCheckpointStore(session=session).resolve(
            run_id=run.id,
            stage_name="infrastructure",
            stage_version="1.0.0",
            input_parts=("snapshot:v1",),
            config_parts=("infrastructure-config:v1",),
            expected_dependencies=("demography", "roads"),
        )

        assert resolution.reusable is None


def test_store_rejects_exact_succeeded_candidate_without_output_fingerprint() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        run = _run(session, project)
        _persist_dependencies(session, run)
        expected = _expected_identity()
        _stage_result(
            session,
            run,
            stage_name="infrastructure",
            input_hash=expected.input_hash,
            config_hash=expected.config_hash,
            output_payload=None,
        )
        session.commit()

        with pytest.raises(
            PersistedCheckpointError,
            match="checkpoint is missing output_fingerprint",
        ):
            SqlAlchemyCheckpointStore(session=session).resolve(
                run_id=run.id,
                stage_name="infrastructure",
                stage_version="1.0.0",
                input_parts=("snapshot:v1",),
                config_parts=("infrastructure-config:v1",),
                expected_dependencies=("demography", "roads"),
            )


def test_store_rejects_missing_dependency_output_fingerprint() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        run = _run(session, project)
        _persist_dependencies(session, run, roads_output=None)
        session.commit()

        with pytest.raises(
            PersistedCheckpointError,
            match="dependency stage is missing output_fingerprint: roads",
        ):
            SqlAlchemyCheckpointStore(session=session).resolve(
                run_id=run.id,
                stage_name="infrastructure",
                stage_version="1.0.0",
                input_parts=("snapshot:v1",),
                config_parts=("infrastructure-config:v1",),
                expected_dependencies=("demography", "roads"),
            )


def test_store_rejects_non_succeeded_dependency() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        run = _run(session, project)
        _persist_dependencies(session, run, roads_status="failed")
        session.commit()

        with pytest.raises(
            PersistedCheckpointError,
            match="dependency stage is not succeeded: roads",
        ):
            SqlAlchemyCheckpointStore(session=session).resolve(
                run_id=run.id,
                stage_name="infrastructure",
                stage_version="1.0.0",
                input_parts=("snapshot:v1",),
                config_parts=("infrastructure-config:v1",),
                expected_dependencies=("demography", "roads"),
            )


def test_store_does_not_reuse_checkpoint_from_another_run() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        first_run = _run(session, project)
        second_run = _run(session, project)
        _persist_dependencies(session, first_run)
        _persist_dependencies(session, second_run)
        expected = _expected_identity()
        _stage_result(
            session,
            first_run,
            stage_name="infrastructure",
            input_hash=expected.input_hash,
            config_hash=expected.config_hash,
            output_payload="other-run-output",
        )
        session.commit()

        resolution = SqlAlchemyCheckpointStore(session=session).resolve(
            run_id=second_run.id,
            stage_name="infrastructure",
            stage_version="1.0.0",
            input_parts=("snapshot:v1",),
            config_parts=("infrastructure-config:v1",),
            expected_dependencies=("demography", "roads"),
        )

        assert resolution.reusable is None


def test_store_requires_every_declared_dependency_row() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        run = _run(session, project)
        _stage_result(
            session,
            run,
            stage_name="demography",
            output_payload="demography-output",
        )
        session.commit()

        with pytest.raises(
            PersistedCheckpointError,
            match="missing persisted dependency stage result: roads",
        ):
            SqlAlchemyCheckpointStore(session=session).resolve(
                run_id=run.id,
                stage_name="infrastructure",
                stage_version="1.0.0",
                input_parts=("snapshot:v1",),
                config_parts=("infrastructure-config:v1",),
                expected_dependencies=("demography", "roads"),
            )
