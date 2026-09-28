"""Bounded run control and progress reads; PostgreSQL remains authoritative.

The API never submits directly to Redis: all new work uses the existing
GenerationRun -> Job -> JobOutbox transaction. Manual retry is a NEW run
with the source input snapshot; it never resets a failed/cancelled record.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.generation import GenerationExecutionError
from backend.app.application.scenario_matrix import ScenarioConfigVariant, ScenarioMatrixError
from backend.app.db.generation_state import SqlAlchemyGenerationStateStore
from backend.app.db.session import SessionLocal
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from backend.app.schemas.run_control import RunJobRead, RunListRead, RunStageRead, RunStateRead

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_SEED = (1 << 63) - 1
_MAX_DATASET_VERSIONS = 32
_TERMINAL_RETRYABLE = frozenset({"failed", "cancelled"})


class RunControlNotFound(ValueError):
    """Project or project-owned run does not exist."""


class RunControlConflict(ValueError):
    """Persisted inputs or lifecycle do not allow the requested transition."""


class SqlAlchemyRunControlService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _project(session: Session, project_id: uuid.UUID) -> Project:
        project = session.get(Project, project_id)
        if project is None:
            raise RunControlNotFound("project not found")
        return project

    @staticmethod
    def _check_boundary(project: Project) -> None:
        if project.boundary is None or project.boundary.srid != project.working_srid:
            raise RunControlConflict("project boundary must use its metric working SRID")

    @staticmethod
    def _versions(
        session: Session,
        *,
        project_id: uuid.UUID,
        version_ids: tuple[uuid.UUID, ...],
    ) -> list[DatasetVersion]:
        if (
            not 1 <= len(version_ids) <= _MAX_DATASET_VERSIONS
            or len(set(version_ids)) != len(version_ids)
        ):
            raise RunControlConflict("run requires 1–32 distinct dataset versions")
        versions = session.scalars(
            select(DatasetVersion)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(
                DatasetVersion.id.in_(version_ids),
                Dataset.project_id == project_id,
                DatasetVersion.status == "ready",
            )
            .order_by(DatasetVersion.id)
            .with_for_update(of=DatasetVersion, read=True)
        ).all()
        if len(versions) != len(version_ids):
            raise RunControlConflict("dataset versions must exist, be ready and belong to project")
        return list(versions)

    @staticmethod
    def _enqueue(session: Session, *, run: GenerationRun) -> None:
        session.add(run)
        session.flush()
        job = Job(
            id=uuid.uuid4(),
            project_id=run.project_id,
            run_id=run.id,
            job_type="generation_run",
            idempotency_key=f"run:{run.id}",
            status="queued",
            attempt_count=0,
            max_attempts=3,
        )
        session.add(job)
        session.flush()
        session.add(
            JobOutbox(
                job_id=job.id,
                queue_name="generation",
                payload={"task": "run_generation", "run_id": str(run.id)},
                status="pending",
            )
        )
        session.flush()

    def create(
        self,
        *,
        project_id: uuid.UUID,
        mode: str,
        seed: int,
        dataset_version_ids: tuple[uuid.UUID, ...],
        config_json: Mapping[str, object],
        config_schema_version: str,
        commit_sha: str,
    ) -> RunStateRead:
        if mode not in {"EXPANSION", "FROM_SCRATCH"}:
            raise RunControlConflict("invalid run mode")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _MAX_SEED:
            raise RunControlConflict("seed is outside persisted BIGINT range")
        if (
            not config_schema_version
            or not config_schema_version.strip()
            or len(config_schema_version) > 64
        ):
            raise RunControlConflict("invalid config schema version")
        if _COMMIT_RE.fullmatch(commit_sha) is None:
            raise RunControlConflict("commit_sha must be a real lowercase 40-hex revision")
        try:
            config = ScenarioConfigVariant.from_config(
                name="manual_run", config_json=config_json
            )
        except ScenarioMatrixError as exc:
            raise RunControlConflict("config must be a finite canonical JSON object") from exc

        with self._session_factory() as session:
            with session.begin():
                project = self._project(session, project_id)
                self._check_boundary(project)
                versions = self._versions(
                    session, project_id=project_id, version_ids=dataset_version_ids
                )
                run = GenerationRun(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    status="queued",
                    mode=mode,
                    seed=seed,
                    working_srid=project.working_srid,
                    config_json=config.config_json,
                    config_schema_version=config_schema_version,
                    commit_sha=commit_sha,
                    dataset_versions=versions,
                )
                self._enqueue(session, run=run)
                run_id = run.id
        return self.get(project_id=project_id, run_id=run_id)

    def retry(self, *, project_id: uuid.UUID, run_id: uuid.UUID) -> RunStateRead:
        """Idempotent manual retry for a terminal failed/cancelled source.

        A source row lock serializes concurrent requests. A distinct linked run
        receives a fresh job and outbox; original diagnostics remain unchanged.
        """
        with self._session_factory() as session:
            with session.begin():
                project = self._project(session, project_id)
                source = session.scalar(
                    select(GenerationRun)
                    .where(GenerationRun.id == run_id, GenerationRun.project_id == project_id)
                    .with_for_update()
                )
                if source is None:
                    raise RunControlNotFound("run not found in project")
                if source.status not in _TERMINAL_RETRYABLE:
                    raise RunControlConflict("manual retry requires a failed or cancelled run")
                jobs = session.scalars(
                    select(Job)
                    .where(Job.run_id == source.id, Job.job_type == "generation_run")
                    .with_for_update()
                ).all()
                if len(jobs) != 1 or jobs[0].status != source.status:
                    raise RunControlConflict("source run/job terminal states are inconsistent")
                existing_id = session.scalar(
                    select(GenerationRun.id)
                    .where(GenerationRun.rerun_source_id == source.id)
                    .order_by(GenerationRun.created_at, GenerationRun.id)
                    .limit(1)
                )
                if existing_id is not None:
                    new_id = existing_id
                else:
                    self._check_boundary(project)
                    if source.working_srid != project.working_srid:
                        raise RunControlConflict("source working SRID has changed")
                    if source.commit_sha is None or _COMMIT_RE.fullmatch(source.commit_sha) is None:
                        raise RunControlConflict("source code revision is missing")
                    try:
                        config = ScenarioConfigVariant.from_config(
                            name="manual_retry", config_json=source.config_json
                        )
                    except ScenarioMatrixError as exc:
                        raise RunControlConflict("source config cannot be replayed") from exc
                    version_ids = tuple(version.id for version in source.dataset_versions)
                    versions = self._versions(
                        session, project_id=project_id, version_ids=version_ids
                    )
                    new_id = uuid.uuid4()
                    self._enqueue(
                        session,
                        run=GenerationRun(
                            id=new_id,
                            project_id=project_id,
                            rerun_source_id=source.id,
                            status="queued",
                            mode=source.mode,
                            seed=source.seed,
                            working_srid=source.working_srid,
                            config_json=config.config_json,
                            config_schema_version=source.config_schema_version,
                            commit_sha=source.commit_sha,
                            dataset_versions=versions,
                        ),
                    )
        return self.get(project_id=project_id, run_id=new_id)

    def cancel(self, *, project_id: uuid.UUID, run_id: uuid.UUID) -> RunStateRead:
        # Scope check precedes the existing worker-compatible one-way DB signal.
        self.get(project_id=project_id, run_id=run_id)
        try:
            SqlAlchemyGenerationStateStore(
                session_factory=self._session_factory
            ).request_cancel(run_id=run_id)
        except GenerationExecutionError as exc:
            raise RunControlConflict("run/job state is inconsistent for cancellation") from exc
        return self.get(project_id=project_id, run_id=run_id)

    @staticmethod
    def _snapshot(
        run: GenerationRun,
        job: Job | None,
        stages: list[RunStageResult],
    ) -> RunStateRead:
        message = run.error_json.get("message") if isinstance(run.error_json, dict) else None
        return RunStateRead(
            id=run.id,
            project_id=run.project_id,
            rerun_source_id=run.rerun_source_id,
            status=run.status,
            mode=run.mode,
            seed=run.seed,
            job=(
                RunJobRead(
                    id=job.id,
                    status=job.status,
                    attempt_count=job.attempt_count,
                    max_attempts=job.max_attempts,
                    cancel_requested_at=job.cancel_requested_at,
                    error_class=job.error_class,
                    error_code=job.error_code,
                )
                if job is not None else None
            ),
            stages=[
                RunStageRead(
                    stage_name=stage.stage_name,
                    stage_version=stage.stage_version,
                    status=stage.status,
                    progress_percent=stage.progress_percent,
                    started_at=stage.started_at,
                    finished_at=stage.finished_at,
                )
                for stage in stages
            ],
            error_message=message if isinstance(message, str) else None,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )

    @staticmethod
    def _project_runs(
        session: Session, *, project_id: uuid.UUID, runs: list[GenerationRun]
    ) -> list[RunStateRead]:
        if not runs:
            return []
        ids = [run.id for run in runs]
        jobs = session.scalars(
            select(Job)
            .where(Job.run_id.in_(ids), Job.job_type == "generation_run")
            .order_by(Job.id)
        ).all()
        by_run: dict[uuid.UUID, Job] = {}
        for job in jobs:
            if job.run_id in by_run:
                raise RunControlConflict("run has multiple authoritative generation jobs")
            if job.project_id != project_id or job.run_id is None:
                raise RunControlConflict("generation job has foreign-project ownership")
            by_run[job.run_id] = job
        stages = session.scalars(
            select(RunStageResult)
            .where(RunStageResult.run_id.in_(ids))
            .order_by(RunStageResult.created_at, RunStageResult.stage_name)
        ).all()
        by_stage: dict[uuid.UUID, list[RunStageResult]] = {run_id: [] for run_id in ids}
        for stage in stages:
            by_stage[stage.run_id].append(stage)
        return [
            SqlAlchemyRunControlService._snapshot(run, by_run.get(run.id), by_stage[run.id])
            for run in runs
        ]

    def get(self, *, project_id: uuid.UUID, run_id: uuid.UUID) -> RunStateRead:
        with self._session_factory() as session:
            self._project(session, project_id)
            run = session.scalar(
                select(GenerationRun).where(
                    GenerationRun.id == run_id,
                    GenerationRun.project_id == project_id,
                )
            )
            if run is None:
                raise RunControlNotFound("run not found in project")
            return self._project_runs(session, project_id=project_id, runs=[run])[0]

    def list(self, *, project_id: uuid.UUID, limit: int = 50) -> RunListRead:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be 1–100")
        with self._session_factory() as session:
            self._project(session, project_id)
            rows = session.scalars(
                select(GenerationRun)
                .where(GenerationRun.project_id == project_id)
                .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
                .limit(limit + 1)
            ).all()
            return RunListRead(
                project_id=project_id,
                limit=limit,
                truncated=len(rows) > limit,
                runs=self._project_runs(session, project_id=project_id, runs=list(rows[:limit])),
            )
