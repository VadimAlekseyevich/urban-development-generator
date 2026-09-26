from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.adapters.checkpoints import SqlAlchemyCheckpointStore
from backend.app.application.checkpoints import (
    CheckpointIdentity,
    build_config_hash,
    build_resolved_input_hash,
)
from backend.app.application.generation import (
    GenerationClaimDisposition,
    GenerationExecutionError,
    StageInvocation,
)
from backend.app.db.session import SessionLocal
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import StageResult
from core.urban_generator.domain.errors import PermanentError
from core.urban_generator.stages.registry import StageAny, StageSkipReason

_GENERATION_JOB_TYPE = "generation_run"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class SqlAlchemyGenerationStateStore:
    """Persist each DAG transition independently in PostgreSQL, never in core."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def claim(self, *, run_id: uuid.UUID) -> GenerationClaimDisposition:
        with self._session_factory() as session:
            with session.begin():
                run = self._run(session, run_id)
                job = self._job(session, run)
                if run.status == "succeeded" and job.status == "succeeded":
                    return GenerationClaimDisposition.ALREADY_SUCCEEDED
                if run.status == "running" and job.status == "running":
                    return GenerationClaimDisposition.IN_PROGRESS
                if run.status != "queued" or job.status != "queued":
                    raise GenerationExecutionError(
                        "generation requires matching queued run and job states"
                    )
                if job.attempt_count >= job.max_attempts:
                    raise GenerationExecutionError("generation job attempt budget exhausted")
                if run.commit_sha is None or _COMMIT_RE.fullmatch(run.commit_sha) is None:
                    raise GenerationExecutionError(
                        "generation requires a persisted 40-character code commit SHA"
                    )

                now = self._clock()
                run.status = "running"
                run.started_at = now
                run.finished_at = None
                run.error_json = None
                job.status = "running"
                job.attempt_count += 1
                job.started_at = now
                job.finished_at = None
                job.error_class = None
                job.error_code = None
                job.error_json = None
                return GenerationClaimDisposition.STARTED

    def resolve_identity(
        self,
        *,
        run_id: uuid.UUID,
        stage: StageAny,
        invocation: StageInvocation,
    ) -> CheckpointIdentity:
        with self._session_factory() as session:
            resolution = SqlAlchemyCheckpointStore(session=session).resolve(
                run_id=run_id,
                stage_name=stage.name,
                stage_version=stage.version,
                input_parts=invocation.input_parts,
                config_parts=invocation.config_parts,
                expected_dependencies=stage.dependencies,
            )
            if resolution.reusable is not None:
                raise GenerationExecutionError(
                    "persisted checkpoint output cannot be used without typed output hydration"
                )
            return resolution.identity

    def start_stage(
        self,
        *,
        run_id: uuid.UUID,
        identity: CheckpointIdentity,
    ) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._require_running(session, run_id)
                existing = session.scalar(
                    select(RunStageResult)
                    .where(
                        RunStageResult.run_id == run_id,
                        RunStageResult.stage_name == identity.stage_name,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    raise GenerationExecutionError(
                        f"stage result already exists: {identity.stage_name}"
                    )
                session.add(
                    RunStageResult(
                        run_id=run_id,
                        stage_name=identity.stage_name,
                        stage_version=identity.stage_version,
                        status="running",
                        progress_percent=0,
                        input_hash=identity.input_hash,
                        config_hash=identity.config_hash,
                        output_fingerprint=None,
                        diagnostics_json=[],
                        artifact_refs_json=[],
                        started_at=self._clock(),
                    )
                )

    def complete_stage(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        result: StageResult[object],
    ) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._require_running(session, run_id)
                stage_row = self._stage(session, run_id, stage_name)
                if stage_row.status != "running":
                    raise GenerationExecutionError(
                        f"stage must be running before completion: {stage_name}"
                    )
                stage_row.status = "succeeded"
                stage_row.progress_percent = 100
                stage_row.output_fingerprint = result.fingerprint.value
                stage_row.diagnostics_json = [
                    {
                        "code": item.code,
                        "message": item.message,
                        "level": item.level.value,
                    }
                    for item in result.diagnostics
                ]
                stage_row.finished_at = self._clock()

    def skip_stage(
        self,
        *,
        run_id: uuid.UUID,
        stage: StageAny,
        reason: StageSkipReason,
        blocked_by: tuple[str, ...],
    ) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._require_running(session, run_id)
                previous = session.scalar(
                    select(RunStageResult).where(
                        RunStageResult.run_id == run_id,
                        RunStageResult.stage_name == stage.name,
                    )
                )
                if previous is not None:
                    raise GenerationExecutionError(
                        f"stage result already exists: {stage.name}"
                    )
                now = self._clock()
                session.add(
                    RunStageResult(
                        run_id=run_id,
                        stage_name=stage.name,
                        stage_version=stage.version,
                        status="skipped",
                        progress_percent=100,
                        input_hash=build_resolved_input_hash(
                            input_parts=(
                                "stage-skip:v1",
                                reason.value,
                                stage.name,
                                *blocked_by,
                            ),
                            expected_dependencies=(),
                            dependency_outputs=(),
                        ),
                        config_hash=build_config_hash("stage-skip:v1"),
                        output_fingerprint=None,
                        diagnostics_json=[
                            {
                                "code": "stage.skipped",
                                "message": reason.value,
                                "level": "INFO",
                                "blocked_by": list(blocked_by),
                            }
                        ],
                        artifact_refs_json=[],
                        started_at=now,
                        finished_at=now,
                    )
                )

    def fail_stage(self, *, run_id: uuid.UUID, stage_name: str) -> None:
        with self._session_factory() as session:
            with session.begin():
                run = self._run(session, run_id)
                if run.status != "running":
                    return
                stage_row = session.scalar(
                    select(RunStageResult)
                    .where(
                        RunStageResult.run_id == run_id,
                        RunStageResult.stage_name == stage_name,
                    )
                    .with_for_update()
                )
                if stage_row is None or stage_row.status != "running":
                    return
                stage_row.status = "failed"
                stage_row.finished_at = self._clock()
                stage_row.diagnostics_json = [
                    {
                        "code": "stage.execution_failed",
                        "message": "stage execution failed",
                        "level": "WARNING",
                    }
                ]

    def complete_run(
        self,
        *,
        run_id: uuid.UUID,
        expected_stage_names: tuple[str, ...],
    ) -> None:
        with self._session_factory() as session:
            with session.begin():
                run = self._require_running(session, run_id)
                rows = session.scalars(
                    select(RunStageResult).where(RunStageResult.run_id == run_id)
                ).all()
                if (
                    len(rows) != len(expected_stage_names)
                    or {row.stage_name for row in rows} != set(expected_stage_names)
                    or any(
                        row.status not in {"succeeded", "skipped"}
                        or (row.status == "succeeded" and row.output_fingerprint is None)
                        for row in rows
                    )
                ):
                    raise GenerationExecutionError(
                        "cannot complete generation with missing or incomplete stage results"
                    )
                if run.commit_sha is None:
                    raise GenerationExecutionError(
                        "cannot complete generation without code commit SHA"
                    )
                job = self._job(session, run)
                if job.status != "running":
                    raise GenerationExecutionError(
                        "generation job must be running before completion"
                    )
                now = self._clock()
                run.status = "succeeded"
                run.finished_at = now
                job.status = "succeeded"
                job.finished_at = now
                job.error_class = None
                job.error_code = None
                job.error_json = None

    def fail_run(self, *, run_id: uuid.UUID, stage_name: str | None) -> None:
        with self._session_factory() as session:
            with session.begin():
                run = self._run(session, run_id)
                if run.status != "running":
                    return
                job = self._job(session, run)
                failure = PermanentError(
                    "generation execution failed",
                    details={
                        "stage_name": stage_name or "assembly",
                    },
                )
                now = self._clock()
                run.status = "failed"
                run.finished_at = now
                run.error_json = {
                    "error_class": failure.category.value,
                    "error_code": failure.code.value,
                    "message": failure.message,
                    "details": dict(failure.details),
                }
                job.record_failure(failure)
                job.finished_at = now

    @staticmethod
    def _run(session: Session, run_id: uuid.UUID) -> GenerationRun:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        run = session.scalar(
            select(GenerationRun)
            .where(GenerationRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise GenerationExecutionError(f"generation run does not exist: {run_id}")
        return run

    @staticmethod
    def _job(session: Session, run: GenerationRun) -> Job:
        jobs = session.scalars(
            select(Job)
            .where(
                Job.run_id == run.id,
                Job.job_type == _GENERATION_JOB_TYPE,
            )
            .with_for_update()
        ).all()
        if len(jobs) != 1:
            raise GenerationExecutionError(
                "generation requires exactly one matching DB-authoritative job"
            )
        job = jobs[0]
        if job.project_id != run.project_id:
            raise GenerationExecutionError(
                "generation job project does not own the run"
            )
        return job

    def _require_running(self, session: Session, run_id: uuid.UUID) -> GenerationRun:
        run = self._run(session, run_id)
        if run.status != "running":
            raise GenerationExecutionError("generation run must be running")
        return run

    @staticmethod
    def _stage(session: Session, run_id: uuid.UUID, stage_name: str) -> RunStageResult:
        stage_row = session.scalar(
            select(RunStageResult)
            .where(
                RunStageResult.run_id == run_id,
                RunStageResult.stage_name == stage_name,
            )
            .with_for_update()
        )
        if stage_row is None:
            raise GenerationExecutionError(
                f"stage result does not exist: {stage_name}"
            )
        return stage_row
