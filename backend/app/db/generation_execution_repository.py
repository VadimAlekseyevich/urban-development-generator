from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.checkpoints import (
    CheckpointIdentity,
    build_config_hash,
    build_resolved_input_hash,
)
from backend.app.application.generation_execution import GenerationRunStartDisposition
from backend.app.db.session import SessionLocal
from backend.app.models.generation_run import GenerationRun
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import StageResult
from core.urban_generator.domain.errors import PermanentError, UrbanGeneratorError
from core.urban_generator.stages import StageSkipReason


class SqlAlchemyGenerationProgressRepository:
    """Persist DB-authoritative run/stage progress for one worker execution."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def begin_run(self, run_id: uuid.UUID) -> GenerationRunStartDisposition:
        _require_uuid(run_id)
        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                run = self._load_run(session, run_id, for_update=True)
                if run.status == "succeeded":
                    return GenerationRunStartDisposition.ALREADY_SUCCEEDED
                if run.status != "queued":
                    raise PermanentError(
                        "generation run is not claimable by worker",
                        details={
                            "run_id": str(run_id),
                            "status": run.status,
                        },
                    )
                run.status = "running"
                run.started_at = now
                run.finished_at = None
                run.error_json = None
        return GenerationRunStartDisposition.STARTED

    def mark_stage_running(
        self,
        *,
        run_id: uuid.UUID,
        identity: CheckpointIdentity,
    ) -> None:
        _require_uuid(run_id)
        if not isinstance(identity, CheckpointIdentity):
            raise TypeError("identity must be CheckpointIdentity")
        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                run = self._load_run(session, run_id, for_update=True)
                self._require_running_run(run)
                row = self._load_stage(
                    session,
                    run_id=run_id,
                    stage_name=identity.stage_name,
                    for_update=True,
                )
                if row is None:
                    row = RunStageResult(
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
                        started_at=now,
                        finished_at=None,
                    )
                    session.add(row)
                    return

                row.stage_version = identity.stage_version
                row.status = "running"
                row.progress_percent = 0
                row.input_hash = identity.input_hash
                row.config_hash = identity.config_hash
                row.output_fingerprint = None
                row.diagnostics_json = []
                row.started_at = now
                row.finished_at = None

    def mark_stage_succeeded(
        self,
        *,
        run_id: uuid.UUID,
        identity: CheckpointIdentity,
        result: StageResult[object],
    ) -> None:
        _require_uuid(run_id)
        if not isinstance(identity, CheckpointIdentity):
            raise TypeError("identity must be CheckpointIdentity")
        if not isinstance(result, StageResult):
            raise TypeError("result must be StageResult")
        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                run = self._load_run(session, run_id, for_update=True)
                self._require_running_run(run)
                row = self._load_stage(
                    session,
                    run_id=run_id,
                    stage_name=identity.stage_name,
                    for_update=True,
                )
                if row is None:
                    raise PermanentError(
                        "stage success has no running persisted stage result",
                        details={
                            "run_id": str(run_id),
                            "stage_name": identity.stage_name,
                        },
                    )
                if row.status != "running":
                    raise PermanentError(
                        "stage must be running before success",
                        details={
                            "stage_name": identity.stage_name,
                            "status": row.status,
                        },
                    )
                if (
                    row.stage_version != identity.stage_version
                    or row.input_hash != identity.input_hash
                    or row.config_hash != identity.config_hash
                ):
                    raise PermanentError(
                        "running stage provenance changed before success",
                        details={"stage_name": identity.stage_name},
                    )

                row.status = "succeeded"
                row.progress_percent = 100
                row.output_fingerprint = result.fingerprint.value
                row.diagnostics_json = [
                    {
                        "code": item.code,
                        "message": item.message,
                        "level": item.level.value,
                    }
                    for item in result.diagnostics
                ]
                row.finished_at = now

    def mark_stage_skipped(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        stage_version: str,
        reason: StageSkipReason,
        blocked_by: tuple[str, ...],
    ) -> None:
        _require_uuid(run_id)
        if not isinstance(reason, StageSkipReason):
            raise TypeError("reason must be StageSkipReason")
        if not isinstance(blocked_by, tuple):
            raise TypeError("blocked_by must be an immutable tuple")
        now = self._clock()
        input_hash = build_resolved_input_hash(
            input_parts=(
                "stage-skip:v1",
                stage_name,
                stage_version,
                reason.value,
                *blocked_by,
            ),
            expected_dependencies=(),
            dependency_outputs=(),
        )
        config_hash = build_config_hash("stage-skip:v1")
        with self._session_factory() as session:
            with session.begin():
                run = self._load_run(session, run_id, for_update=True)
                self._require_running_run(run)
                row = self._load_stage(
                    session,
                    run_id=run_id,
                    stage_name=stage_name,
                    for_update=True,
                )
                if row is None:
                    row = RunStageResult(
                        run_id=run_id,
                        stage_name=stage_name,
                        stage_version=stage_version,
                        status="skipped",
                        progress_percent=100,
                        input_hash=input_hash,
                        config_hash=config_hash,
                        output_fingerprint=None,
                        diagnostics_json=[],
                        artifact_refs_json=[],
                        started_at=None,
                        finished_at=now,
                    )
                    session.add(row)
                else:
                    row.stage_version = stage_version
                    row.status = "skipped"
                    row.progress_percent = 100
                    row.input_hash = input_hash
                    row.config_hash = config_hash
                    row.output_fingerprint = None
                    row.finished_at = now

                row.diagnostics_json = [
                    {
                        "code": "stage.skipped",
                        "message": _skip_message(reason, blocked_by),
                        "level": "INFO",
                    }
                ]

    def mark_stage_failed(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        stage_version: str,
        error: Exception,
    ) -> None:
        _require_uuid(run_id)
        now = self._clock()
        diagnostic = _error_diagnostic(error)
        with self._session_factory() as session:
            with session.begin():
                run = self._load_run(session, run_id, for_update=True)
                if run.status == "succeeded":
                    return
                row = self._load_stage(
                    session,
                    run_id=run_id,
                    stage_name=stage_name,
                    for_update=True,
                )
                if row is None:
                    row = RunStageResult(
                        run_id=run_id,
                        stage_name=stage_name,
                        stage_version=stage_version,
                        status="failed",
                        progress_percent=0,
                        input_hash=build_resolved_input_hash(
                            input_parts=(
                                "unresolved-stage-failure:v1",
                                stage_name,
                                stage_version,
                            ),
                            expected_dependencies=(),
                            dependency_outputs=(),
                        ),
                        config_hash=build_config_hash(
                            "unresolved-stage-failure:v1"
                        ),
                        output_fingerprint=None,
                        diagnostics_json=[diagnostic],
                        artifact_refs_json=[],
                        started_at=None,
                        finished_at=now,
                    )
                    session.add(row)
                    return

                if row.status == "succeeded":
                    return
                row.status = "failed"
                row.output_fingerprint = None
                row.diagnostics_json = [diagnostic]
                row.finished_at = now

    def mark_run_succeeded(
        self,
        *,
        run_id: uuid.UUID,
        commit_sha: str,
    ) -> None:
        _require_uuid(run_id)
        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                run = self._load_run(session, run_id, for_update=True)
                self._require_running_run(run)
                active = session.scalar(
                    select(RunStageResult.id)
                    .where(
                        RunStageResult.run_id == run_id,
                        RunStageResult.status.in_(("pending", "running", "failed")),
                    )
                    .limit(1)
                )
                if active is not None:
                    raise PermanentError(
                        "generation run cannot succeed with non-terminal stage progress",
                        details={"run_id": str(run_id)},
                    )
                run.commit_sha = commit_sha
                run.status = "succeeded"
                run.error_json = None
                run.finished_at = now

    def mark_run_failed(
        self,
        *,
        run_id: uuid.UUID,
        error: Exception,
    ) -> None:
        _require_uuid(run_id)
        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                run = self._load_run(session, run_id, for_update=True)
                if run.status == "succeeded":
                    return
                run.status = "failed"
                run.error_json = _error_payload(error)
                run.finished_at = now

    @staticmethod
    def _load_run(
        session: Session,
        run_id: uuid.UUID,
        *,
        for_update: bool,
    ) -> GenerationRun:
        statement = select(GenerationRun).where(GenerationRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        run = session.scalar(statement)
        if run is None:
            raise PermanentError(
                "generation run does not exist",
                details={"run_id": str(run_id)},
            )
        return run

    @staticmethod
    def _load_stage(
        session: Session,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        for_update: bool,
    ) -> RunStageResult | None:
        statement = select(RunStageResult).where(
            RunStageResult.run_id == run_id,
            RunStageResult.stage_name == stage_name,
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def _require_running_run(run: GenerationRun) -> None:
        if run.status != "running":
            raise PermanentError(
                "generation run must be running for stage progress",
                details={
                    "run_id": str(run.id),
                    "status": run.status,
                },
            )


def _skip_message(
    reason: StageSkipReason,
    blocked_by: tuple[str, ...],
) -> str:
    if reason is StageSkipReason.REQUESTED:
        return "stage skipped by explicit execution plan"
    return (
        "stage skipped because dependencies were skipped: "
        + ", ".join(blocked_by)
    )


def _error_diagnostic(error: Exception) -> dict[str, object]:
    if isinstance(error, UrbanGeneratorError):
        return {
            "code": error.code.value,
            "message": error.message,
            "level": "WARNING",
        }
    return {
        "code": "stage.execution_failed",
        "message": str(error) or type(error).__name__,
        "level": "WARNING",
    }


def _error_payload(error: Exception) -> dict[str, object]:
    if isinstance(error, UrbanGeneratorError):
        return {
            "code": error.code.value,
            "category": error.category.value,
            "message": error.message,
            "details": dict(error.details),
            "retryable": error.retryable,
            "cancelled": error.cancelled,
        }
    return {
        "code": "generation.execution_failed",
        "category": "permanent",
        "message": str(error) or type(error).__name__,
        "retryable": False,
        "cancelled": False,
    }


def _require_uuid(value: uuid.UUID) -> None:
    if not isinstance(value, uuid.UUID):
        raise TypeError("run_id must be UUID")
