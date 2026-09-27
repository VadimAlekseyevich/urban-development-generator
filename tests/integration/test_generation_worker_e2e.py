"""Real worker entrypoint -> PostgreSQL generation lifecycle acceptance fixture.

This uses deliberately tiny canonical Stage implementations to exercise the execution
boundary without substituting mocked persistence or rerunning expensive GIS algorithms.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from arq import Retry
from geoalchemy2.elements import WKTElement
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.app.adapters import LocalArtifactStore, SqlAlchemyGenerationRuntimeFactory
from backend.app.application.checkpoints import (
    DependencyOutputFingerprint,
    build_config_hash,
    build_resolved_input_hash,
)
from backend.app.application.generation import (
    GenerationJobService,
    GenerationRetryScheduled,
    StageInvocation,
)
from backend.app.db.generation_state import SqlAlchemyGenerationStateStore
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import (
    ConfigRef,
    PipelineContext,
    ResolvedConfigBinding,
    RunContext,
    StageDiagnostic,
    StageFingerprint,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
)
from core.urban_generator.domain.errors import TransientError, UrbanGeneratorError
from core.urban_generator.stages import StageRegistry
from core.urban_generator.stages.registry import StageAny
from worker.tasks import GenerationTaskFailed, run_generation

WORKING_SRID = 32637
COMMIT_SHA = "a" * 40
STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "root": (),
    "alpha": ("root",),
    "beta": ("root",),
    "joined": ("beta", "alpha"),
}
TOPOLOGICAL_NAMES = ("root", "alpha", "beta", "joined")
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@dataclass(frozen=True, slots=True)
class FixtureStageConfig:
    token: str


@dataclass(slots=True)
class ExecutionProbe:
    run_id: uuid.UUID
    job_id: uuid.UUID
    calls: list[str] = field(default_factory=list)
    seen_inputs: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    cancel_on_stage: str | None = None


@dataclass(slots=True)
class FixtureStage:
    name: str
    dependencies: tuple[str, ...]
    probe: ExecutionProbe
    fail: bool = False
    failure: UrbanGeneratorError | None = None
    version: str = "fixture-v1"

    def validate_input(self, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("fixture stage input must be int")
        return value

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: int,
        config: FixtureStageConfig,
    ) -> StageResult[int]:
        assert context.run_id == self.probe.run_id
        assert snapshot.snapshot_id == context.run_id
        assert snapshot.settings.working_srid == context.working_srid == WORKING_SRID
        assert len(snapshot.roads) == 1
        assert config.token == f"fixture:{self.name}"
        # Stage rows must be committed and observable from a *different* DB session
        # while their algorithm is running, before the worker reports final success.
        with SessionFactory() as session:
            run = session.get(GenerationRun, self.probe.run_id)
            job = session.get(Job, self.probe.job_id)
            active = session.scalar(
                select(RunStageResult).where(
                    RunStageResult.run_id == self.probe.run_id,
                    RunStageResult.stage_name == self.name,
                )
            )
            assert run is not None and run.status == "running"
            assert job is not None and job.status == "running"
            assert active is not None
            assert (active.status, active.progress_percent) == ("running", 0)
            assert active.output_fingerprint is None
            for dependency in self.dependencies:
                completed = session.scalar(
                    select(RunStageResult).where(
                        RunStageResult.run_id == self.probe.run_id,
                        RunStageResult.stage_name == dependency,
                    )
                )
                assert completed is not None
                assert completed.status == "succeeded"
                assert completed.output_fingerprint is not None

        self.probe.calls.append(self.name)
        if self.probe.cancel_on_stage == self.name:
            assert SqlAlchemyGenerationStateStore(
                session_factory=SessionFactory
            ).request_cancel(run_id=self.probe.run_id)
        if self.failure is not None:
            raise self.failure
        if self.fail:
            raise RuntimeError("injected stage execution failure")
        return StageResult(
            output=stage_input + 1,
            fingerprint=build_stage_fingerprint(
                "worker-integration:v1", self.name, str(stage_input), config.token
            ),
            diagnostics=(
                StageDiagnostic(code="fixture.executed", message=self.name),
            ),
        )


class FixtureConfigResolver:
    def __init__(self, stage_names: tuple[str, ...]) -> None:
        self._stage_names = stage_names

    def resolve(
        self,
        *,
        config_json: Mapping[str, object],
        schema_version: str,
        source: ConfigRef,
    ) -> tuple[ResolvedConfigBinding, ...]:
        assert schema_version == "fixture-v1"
        assert config_json == {"scenario": "worker-integration"}
        return tuple(
            ResolvedConfigBinding(
                stage_name=name,
                source=source,
                value=FixtureStageConfig(token=f"fixture:{name}"),
            )
            for name in self._stage_names
        )


class FixtureInputResolver:
    def __init__(self, probe: ExecutionProbe) -> None:
        self._probe = probe

    def resolve(
        self,
        *,
        stage: StageAny,
        context: PipelineContext,
        config: object,
        outputs: Mapping[str, object],
    ) -> StageInvocation:
        assert context.run.run_id == self._probe.run_id
        assert isinstance(config, FixtureStageConfig)
        self._probe.seen_inputs.append((stage.name, tuple(sorted(outputs))))
        value = 1 + sum(int(outputs[name]) for name in stage.dependencies)
        return StageInvocation(
            stage_input=value,
            input_parts=(f"fixture-input:{stage.name}", str(value)),
            config_parts=(config.token,),
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


def _setup(
    *, boundary: bool = True, max_attempts: int = 3
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    with SessionFactory() as session:
        project = Project(
            name="Generation worker integration",
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
            version=1,
            status="ready",
            checksum_sha256="b" * 64,
            source_metadata={"source": "synthetic-roads"},
        )
        session.add(version)
        session.flush()
        run = GenerationRun(
            project_id=project.id,
            mode="EXPANSION",
            status="queued",
            seed=17,
            working_srid=WORKING_SRID,
            config_json={"scenario": "worker-integration"},
            config_schema_version="fixture-v1",
            commit_sha=COMMIT_SHA,
            dataset_versions=[version],
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
            max_attempts=max_attempts,
        )
        session.add(job)
        session.commit()
        return run.id, job.id, version.id


def _factory(
    tmp_path: Path,
    probe: ExecutionProbe,
    *,
    fail_stage: str | None = None,
    stage_error: UrbanGeneratorError | None = None,
    config_resolver: FixtureConfigResolver | None = None,
    skip_stages: tuple[str, ...] = (),
) -> SqlAlchemyGenerationRuntimeFactory:
    names = TOPOLOGICAL_NAMES
    return SqlAlchemyGenerationRuntimeFactory(
        session_factory=SessionFactory,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        config_resolver=config_resolver or FixtureConfigResolver(names),
        registry=StageRegistry(
            stages=tuple(
                FixtureStage(
                    name=name,
                    dependencies=STAGE_DEPENDENCIES[name],
                    probe=probe,
                    fail=name == fail_stage and stage_error is None,
                    failure=stage_error if name == fail_stage else None,
                )
                for name in reversed(names)
            )
        ),
        input_resolver=FixtureInputResolver(probe),
        skip_stages=skip_stages,
    )


def _persisted(run_id: uuid.UUID, job_id: uuid.UUID):
    with SessionFactory() as session:
        run = session.get(GenerationRun, run_id)
        job = session.get(Job, job_id)
        stages = session.scalars(
            select(RunStageResult)
            .where(RunStageResult.run_id == run_id)
            .order_by(RunStageResult.stage_name)
        ).all()
        assert run is not None and job is not None
        session.expunge(run)
        session.expunge(job)
        for stage in stages:
            session.expunge(stage)
        return run, job, {stage.stage_name: stage for stage in stages}


def _invoke(
    run_id: uuid.UUID,
    factory: SqlAlchemyGenerationRuntimeFactory,
) -> dict[str, object]:
    # Do not inject a fake GenerationJobService: this exercises the actual worker
    # composition branch and real PostgreSQL state store.
    return asyncio.run(
        run_generation({"generation_runtime_factory": factory}, str(run_id))
    )


def test_worker_success_commits_dag_progress_and_is_idempotent(tmp_path: Path) -> None:
    run_id, job_id, version_id = _setup()
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)
    factory = _factory(tmp_path, probe)

    first = _invoke(run_id, factory)

    assert first == {
        "run_id": str(run_id),
        "status": "succeeded",
        "succeeded_stages": list(TOPOLOGICAL_NAMES),
        "skipped_stages": [],
    }
    assert probe.calls == list(TOPOLOGICAL_NAMES)
    assert probe.seen_inputs == [
        ("root", ()),
        ("alpha", ("root",)),
        ("beta", ("alpha", "root")),
        ("joined", ("alpha", "beta", "root")),
    ]

    run, job, rows = _persisted(run_id, job_id)
    assert run.status == job.status == "succeeded"
    assert (run.seed, run.commit_sha) == (17, COMMIT_SHA)
    assert run.started_at is not None and run.finished_at is not None
    assert job.started_at is not None and job.finished_at is not None
    assert job.attempt_count == 1
    assert job.error_class is None and run.error_json is None
    assert set(rows) == set(TOPOLOGICAL_NAMES)

    stage_inputs = {"root": 1, "alpha": 3, "beta": 3, "joined": 9}
    for name in TOPOLOGICAL_NAMES:
        row = rows[name]
        assert (row.status, row.progress_percent, row.stage_version) == (
            "succeeded", 100, "fixture-v1"
        )
        assert row.started_at is not None and row.finished_at is not None
        assert row.diagnostics_json == [
            {"code": "fixture.executed", "message": name, "level": "INFO"}
        ]
        assert row.artifact_refs_json == []
        assert row.output_fingerprint == build_stage_fingerprint(
            "worker-integration:v1", name, str(stage_inputs[name]), f"fixture:{name}"
        ).value
        expected_dependencies = STAGE_DEPENDENCIES[name]
        assert row.input_hash == build_resolved_input_hash(
            input_parts=(f"fixture-input:{name}", str(stage_inputs[name])),
            expected_dependencies=expected_dependencies,
            dependency_outputs=tuple(
                DependencyOutputFingerprint(
                    stage_name=dependency,
                    output_fingerprint=StageFingerprint(
                        value=rows[dependency].output_fingerprint
                    ),
                )
                for dependency in expected_dependencies
            ),
        )
        assert row.config_hash == build_config_hash(f"fixture:{name}")

    with SessionFactory() as session:
        persisted_run = session.get(GenerationRun, run_id)
        assert persisted_run is not None
        assert tuple(version.id for version in persisted_run.dataset_versions) == (version_id,)

    second = _invoke(run_id, factory)
    assert second == {
        "run_id": str(run_id),
        "status": "already_succeeded",
        "succeeded_stages": [],
        "skipped_stages": [],
    }
    assert probe.calls == list(TOPOLOGICAL_NAMES)
    repeated_run, repeated_job, repeated_rows = _persisted(run_id, job_id)
    assert repeated_run.status == repeated_job.status == "succeeded"
    assert repeated_job.attempt_count == 1
    assert {
        name: (row.id, row.output_fingerprint, row.input_hash, row.config_hash)
        for name, row in repeated_rows.items()
    } == {
        name: (row.id, row.output_fingerprint, row.input_hash, row.config_hash)
        for name, row in rows.items()
    }


def test_worker_failure_preserves_previous_stage_progress_without_false_success(
    tmp_path: Path,
) -> None:
    run_id, job_id, _ = _setup()
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)

    with pytest.raises(GenerationTaskFailed, match=str(run_id)):
        _invoke(run_id, _factory(tmp_path, probe, fail_stage="beta"))

    assert probe.calls == ["root", "alpha", "beta"]
    run, job, rows = _persisted(run_id, job_id)
    assert run.status == job.status == "failed"
    assert job.attempt_count == 1
    assert job.error_class == "permanent"
    assert job.error_json is not None and job.error_json["retryable"] is False
    assert run.error_json is not None
    assert run.error_json["details"] == {"stage_name": "beta"}
    assert run.finished_at is not None and job.finished_at is not None
    assert set(rows) == {"root", "alpha", "beta"}
    for name in ("root", "alpha"):
        assert rows[name].status == "succeeded"
        assert rows[name].progress_percent == 100
        assert rows[name].output_fingerprint is not None
    assert rows["beta"].status == "failed"
    assert rows["beta"].progress_percent == 0
    assert rows["beta"].output_fingerprint is None
    assert rows["beta"].diagnostics_json[0]["code"] == "stage.execution_failed"


def test_worker_assembly_failure_is_committed_without_stage_success(tmp_path: Path) -> None:
    run_id, job_id, _ = _setup(boundary=False)
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)

    with pytest.raises(GenerationTaskFailed, match=str(run_id)):
        _invoke(run_id, _factory(tmp_path, probe))

    run, job, rows = _persisted(run_id, job_id)
    assert run.status == job.status == "failed"
    assert run.error_json is not None
    assert run.error_json["details"] == {"stage_name": "assembly"}
    assert job.attempt_count == 1
    assert not rows and not probe.calls


def test_worker_explicit_skip_persists_reason_without_fake_output(tmp_path: Path) -> None:
    run_id, job_id, _ = _setup()
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)

    result = _invoke(run_id, _factory(tmp_path, probe, skip_stages=("alpha",)))

    assert result["status"] == "succeeded"
    assert result["succeeded_stages"] == ["root", "beta"]
    assert result["skipped_stages"] == ["alpha", "joined"]
    assert probe.calls == ["root", "beta"]
    run, job, rows = _persisted(run_id, job_id)
    assert run.status == job.status == "succeeded"
    assert rows["alpha"].diagnostics_json[0] == {
        "code": "stage.skipped",
        "message": "REQUESTED",
        "level": "INFO",
        "blocked_by": [],
    }
    assert rows["joined"].diagnostics_json[0] == {
        "code": "stage.skipped",
        "message": "DEPENDENCY_SKIPPED",
        "level": "INFO",
        "blocked_by": ["alpha"],
    }
    for name in ("alpha", "joined"):
        assert rows[name].status == "skipped"
        assert rows[name].progress_percent == 100
        assert rows[name].output_fingerprint is None


def test_successful_worker_results_are_database_immutable(tmp_path: Path) -> None:
    run_id, job_id, version_id = _setup()
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)
    _invoke(run_id, _factory(tmp_path, probe))
    initial_run, initial_job, initial_rows = _persisted(run_id, job_id)

    for statement in (
        "UPDATE generation_runs SET seed = seed + 1 WHERE id = :run_id",
        "UPDATE run_stage_results SET output_fingerprint = NULL "
        "WHERE run_id = :run_id AND stage_name = 'root'",
        "DELETE FROM run_stage_results WHERE run_id = :run_id AND stage_name = 'root'",
        "DELETE FROM generation_run_dataset_versions "
        "WHERE run_id = :run_id AND dataset_version_id = :version_id",
    ):
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text(statement), {"run_id": run_id, "version_id": version_id}
                )

    run, job, rows = _persisted(run_id, job_id)
    assert run.seed == initial_run.seed == 17
    assert run.status == job.status == initial_job.status == "succeeded"
    assert rows.keys() == initial_rows.keys()
    assert all(
        rows[name].output_fingerprint == old.output_fingerprint
        for name, old in initial_rows.items()
    )
    with SessionFactory() as session:
        persisted = session.get(GenerationRun, run_id)
        assert persisted is not None
        assert tuple(version.id for version in persisted.dataset_versions) == (version_id,)



def test_cancelled_queued_worker_does_not_start_or_assemble_runtime(
    tmp_path: Path,
) -> None:
    run_id, job_id, _ = _setup()
    store = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)

    assert store.request_cancel(run_id=run_id)
    assert not store.request_cancel(run_id=run_id)
    result = _invoke(run_id, _factory(tmp_path, probe))

    assert result == {
        "run_id": str(run_id),
        "status": "cancelled",
        "succeeded_stages": [],
        "skipped_stages": [],
    }
    run, job, stages = _persisted(run_id, job_id)
    assert run.status == job.status == "cancelled"
    assert job.attempt_count == 0
    assert run.started_at is None and job.started_at is None
    assert run.finished_at is not None and job.finished_at is not None
    assert job.cancel_requested_at is not None
    assert job.error_class == "cancelled"
    assert job.error_code == "cancelled.error"
    assert job.error_json is not None and job.error_json["retryable"] is False
    assert run.error_json is not None
    assert run.error_json["details"]["stage_name"] == "queued"
    assert stages == {} and probe.calls == []


@pytest.mark.parametrize(
    ("cancel_on_stage", "completed_names"),
    (
        ("alpha", ("root",)),
        ("joined", ("root", "alpha", "beta")),
    ),
)
def test_worker_observes_cancel_during_bounded_stage_and_preserves_provenance(
    tmp_path: Path,
    cancel_on_stage: str,
    completed_names: tuple[str, ...],
) -> None:
    run_id, job_id, _ = _setup()
    probe = ExecutionProbe(
        run_id=run_id,
        job_id=job_id,
        cancel_on_stage=cancel_on_stage,
    )

    result = _invoke(run_id, _factory(tmp_path, probe))

    assert result["status"] == "cancelled"
    assert result["succeeded_stages"] == list(completed_names)
    assert result["skipped_stages"] == []
    assert probe.calls == [*completed_names, cancel_on_stage]
    run, job, stages = _persisted(run_id, job_id)
    assert run.status == job.status == "cancelled"
    assert job.cancel_requested_at is not None
    assert job.attempt_count == 1
    assert job.error_class == "cancelled"
    assert job.error_code == "cancelled.error"
    assert run.error_json is not None
    assert run.error_json["details"]["stage_name"] == cancel_on_stage
    assert set(stages) == {*completed_names, cancel_on_stage}
    for name in completed_names:
        assert stages[name].status == "succeeded"
        assert stages[name].output_fingerprint is not None
    active = stages[cancel_on_stage]
    assert active.status == "cancelled"
    assert active.progress_percent == 0
    assert active.output_fingerprint is None
    assert active.diagnostics_json[0]["code"] == "stage.cancelled"
    assert active.finished_at is not None
    assert not SqlAlchemyGenerationStateStore(
        session_factory=SessionFactory
    ).request_cancel(run_id=run_id)


def test_cancel_request_after_worker_success_is_a_noop(tmp_path: Path) -> None:
    run_id, job_id, _ = _setup()
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)
    _invoke(run_id, _factory(tmp_path, probe))
    before_run, before_job, before_stages = _persisted(run_id, job_id)

    assert not SqlAlchemyGenerationStateStore(
        session_factory=SessionFactory
    ).request_cancel(run_id=run_id)

    after_run, after_job, after_stages = _persisted(run_id, job_id)
    assert after_run.status == after_job.status == "succeeded"
    assert after_job.cancel_requested_at is None
    assert after_job.attempt_count == before_job.attempt_count == 1
    assert after_run.finished_at == before_run.finished_at
    assert {name: row.output_fingerprint for name, row in after_stages.items()} == {
        name: row.output_fingerprint for name, row in before_stages.items()
    }


class FlakyConfigResolver(FixtureConfigResolver):
    def __init__(self, stage_names: tuple[str, ...], *, failures: int) -> None:
        super().__init__(stage_names)
        self.failures = failures
        self.calls = 0

    def resolve(
        self,
        *,
        config_json: Mapping[str, object],
        schema_version: str,
        source: ConfigRef,
    ) -> tuple[ResolvedConfigBinding, ...]:
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientError("temporary config source unavailable")
        return super().resolve(
            config_json=config_json,
            schema_version=schema_version,
            source=source,
        )


def test_worker_retries_stage_free_transient_attempts_with_db_backoff(
    tmp_path: Path,
) -> None:
    run_id, job_id, _ = _setup()
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)
    resolver = FlakyConfigResolver(TOPOLOGICAL_NAMES, failures=2)
    factory = _factory(tmp_path, probe, config_resolver=resolver)
    now = [datetime(2026, 9, 27, 10, 0, tzinfo=UTC)]
    store = SqlAlchemyGenerationStateStore(
        session_factory=SessionFactory, clock=lambda: now[0]
    )
    service = GenerationJobService(state_store=store, runtime_factory=factory)

    def attempt() -> dict[str, object]:
        return asyncio.run(
            run_generation({"generation_job_service": service}, str(run_id))
        )

    for number, delay in ((1, 30), (2, 60)):
        with pytest.raises(Retry):
            attempt()
        run, job, rows = _persisted(run_id, job_id)
        assert run.status == job.status == "queued"
        assert job.attempt_count == number
        assert job.error_class == "transient"
        assert job.error_code == "transient.error"
        assert job.error_json is not None and job.error_json["retryable"] is True
        assert run.error_json is not None and run.error_json["retryable"] is True
        assert rows == {}
        assert resolver.calls == number
        with pytest.raises(GenerationRetryScheduled) as blocked:
            store.claim(run_id=run_id)
        assert blocked.value.delay_seconds == delay
        assert _persisted(run_id, job_id)[1].attempt_count == number
        now[0] += timedelta(seconds=delay)

    result = attempt()
    assert result["status"] == "succeeded"
    assert result["succeeded_stages"] == list(TOPOLOGICAL_NAMES)
    run, job, rows = _persisted(run_id, job_id)
    assert run.status == job.status == "succeeded"
    assert job.attempt_count == job.max_attempts == 3
    assert job.error_class is None and job.error_json is None
    assert run.error_json is None
    assert len(rows) == len(TOPOLOGICAL_NAMES)
    assert resolver.calls == 3
    assert attempt()["status"] == "already_succeeded"
    assert _persisted(run_id, job_id)[1].attempt_count == 3


def test_worker_transient_assembly_failure_exhausts_attempt_budget(
    tmp_path: Path,
) -> None:
    run_id, job_id, _ = _setup(max_attempts=2)
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)
    resolver = FlakyConfigResolver(TOPOLOGICAL_NAMES, failures=3)
    factory = _factory(tmp_path, probe, config_resolver=resolver)
    now = [datetime(2026, 9, 27, 10, 0, tzinfo=UTC)]
    store = SqlAlchemyGenerationStateStore(
        session_factory=SessionFactory, clock=lambda: now[0]
    )
    service = GenerationJobService(state_store=store, runtime_factory=factory)

    with pytest.raises(Retry):
        asyncio.run(run_generation({"generation_job_service": service}, str(run_id)))
    now[0] += timedelta(seconds=30)
    with pytest.raises(GenerationTaskFailed, match=str(run_id)):
        asyncio.run(run_generation({"generation_job_service": service}, str(run_id)))

    run, job, rows = _persisted(run_id, job_id)
    assert run.status == job.status == "failed"
    assert job.attempt_count == job.max_attempts == 2
    assert job.error_class == "transient"
    assert job.error_json is not None and job.error_json["retryable"] is False
    assert run.error_json is not None and run.error_json["retryable"] is False
    assert rows == {} and not probe.calls
    assert resolver.calls == 2


def test_worker_does_not_replay_transient_failure_after_persisted_stage_progress(
    tmp_path: Path,
) -> None:
    run_id, job_id, _ = _setup()
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)
    factory = _factory(
        tmp_path,
        probe,
        fail_stage="beta",
        stage_error=TransientError("temporary stage storage failure"),
    )
    with pytest.raises(GenerationTaskFailed, match=str(run_id)):
        _invoke(run_id, factory)
    run, job, stages = _persisted(run_id, job_id)
    assert run.status == job.status == "failed"
    assert job.attempt_count == 1
    assert job.error_class == "transient"
    assert job.error_json is not None and job.error_json["retryable"] is False
    assert run.error_json is not None and run.error_json["retryable"] is False
    assert stages["root"].status == stages["alpha"].status == "succeeded"
    assert stages["beta"].status == "failed"
    assert stages["beta"].output_fingerprint is None
    assert probe.calls == ["root", "alpha", "beta"]


def test_queued_retry_can_be_cancelled_without_starting_another_attempt(
    tmp_path: Path,
) -> None:
    run_id, job_id, _ = _setup()
    probe = ExecutionProbe(run_id=run_id, job_id=job_id)
    resolver = FlakyConfigResolver(TOPOLOGICAL_NAMES, failures=1)
    factory = _factory(tmp_path, probe, config_resolver=resolver)
    store = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    service = GenerationJobService(state_store=store, runtime_factory=factory)

    with pytest.raises(Retry):
        asyncio.run(run_generation({"generation_job_service": service}, str(run_id)))
    assert store.request_cancel(run_id=run_id)
    assert asyncio.run(
        run_generation({"generation_job_service": service}, str(run_id))
    )["status"] == "cancelled"
    run, job, rows = _persisted(run_id, job_id)
    assert run.status == job.status == "cancelled"
    assert job.attempt_count == 1 and job.error_class == "cancelled"
    assert resolver.calls == 1 and rows == {}
