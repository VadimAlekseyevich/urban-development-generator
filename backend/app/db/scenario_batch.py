"""DB-authoritative ScenarioBatch creation, status reconciliation and admission."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.application.scenario_matrix import (
    ScenarioMatrixSpec,
    expand_scenario_matrix,
)
from backend.app.db.session import SessionLocal
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.models.scenario_batch import ScenarioBatch, ScenarioBatchRun

MIN_BATCH_RUNS = 3
MAX_BATCH_RUNS = 10
BATCH_CAPACITY_RETRY_SECONDS = 30
_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class ScenarioBatchError(ValueError):
    """The batch composition or persisted lifecycle is inconsistent."""


@dataclass(frozen=True, slots=True)
class ScenarioMatrixCreation:
    """New sealed batch and its matrix children in canonical position order."""

    batch_id: uuid.UUID
    run_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class ScenarioBatchSnapshot:
    batch_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    concurrency_limit: int
    run_ids: tuple[uuid.UUID, ...]
    active_runs: int


class SqlAlchemyScenarioBatchStore:
    """Compose a sealed batch and reconcile status from its authoritative child runs."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(
        self,
        *,
        project_id: uuid.UUID,
        run_ids: tuple[uuid.UUID, ...],
        concurrency_limit: int,
    ) -> uuid.UUID:
        """Persist a 3–10 child batch atomically, in explicit stable input order."""

        if not isinstance(project_id, uuid.UUID):
            raise ScenarioBatchError("project_id must be UUID")
        if not isinstance(run_ids, tuple) or not (
            MIN_BATCH_RUNS <= len(run_ids) <= MAX_BATCH_RUNS
        ):
            raise ScenarioBatchError("batch requires 3–10 ordered child run IDs")
        if any(not isinstance(run_id, uuid.UUID) for run_id in run_ids):
            raise ScenarioBatchError("child run IDs must be UUID")
        if len(set(run_ids)) != len(run_ids):
            raise ScenarioBatchError("batch child runs must be distinct")
        if (
            not isinstance(concurrency_limit, int)
            or isinstance(concurrency_limit, bool)
            or not 1 <= concurrency_limit <= len(run_ids)
        ):
            raise ScenarioBatchError("concurrency_limit must be between 1 and child count")

        with self._session_factory() as session:
            with session.begin():
                # Stable lock order prevents competing batch builders from deadlocking.
                children = session.scalars(
                    select(GenerationRun)
                    .where(GenerationRun.id.in_(run_ids))
                    .order_by(GenerationRun.id)
                    .with_for_update()
                ).all()
                if len(children) != len(run_ids):
                    raise ScenarioBatchError("a child generation run does not exist")
                if any(child.project_id != project_id for child in children):
                    raise ScenarioBatchError("batch children must belong to the same project")
                if any(child.status != "queued" for child in children):
                    raise ScenarioBatchError("batch children must be queued")
                linked = session.scalar(
                    select(func.count())
                    .select_from(ScenarioBatchRun)
                    .where(ScenarioBatchRun.run_id.in_(run_ids))
                )
                if linked:
                    raise ScenarioBatchError("a child run already belongs to a batch")
                return self._seal(
                    session,
                    project_id=project_id,
                    run_ids=run_ids,
                    concurrency_limit=concurrency_limit,
                )

    def create_matrix(self, *, spec: ScenarioMatrixSpec) -> ScenarioMatrixCreation:
        """Create full-config × explicit-seed runs, jobs, outbox and batch atomically.

        No Redis call happens in this transaction. The existing outbox dispatcher
        handles repeatable delivery after the committed membership is sealed.
        """

        children = expand_scenario_matrix(spec)
        with self._session_factory() as session:
            with session.begin():
                project = session.scalar(
                    select(Project)
                    .where(Project.id == spec.project_id)
                    .with_for_update(read=True)
                )
                if project is None:
                    raise ScenarioBatchError("matrix project does not exist")
                if project.boundary is None or project.boundary.srid != project.working_srid:
                    raise ScenarioBatchError(
                        "matrix project needs a boundary in its metric working SRID"
                    )
                version_ids = tuple(sorted(spec.dataset_version_ids))
                versions = session.scalars(
                    select(DatasetVersion)
                    .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                    .where(
                        DatasetVersion.id.in_(version_ids),
                        Dataset.project_id == spec.project_id,
                        DatasetVersion.status == "ready",
                    )
                    .order_by(DatasetVersion.id)
                    .with_for_update(of=DatasetVersion, read=True)
                ).all()
                if len(versions) != len(version_ids):
                    raise ScenarioBatchError(
                        "matrix dataset versions must all be ready and project-owned"
                    )

                run_ids: list[uuid.UUID] = []
                jobs: list[Job] = []
                outbox_messages: list[JobOutbox] = []
                for child in children:
                    run_id = uuid.uuid4()
                    job_id = uuid.uuid4()
                    session.add(
                        GenerationRun(
                            id=run_id,
                            project_id=spec.project_id,
                            status="queued",
                            mode=spec.mode.value,
                            seed=child.seed,
                            working_srid=project.working_srid,
                            config_json=child.config_json,
                            config_schema_version=spec.config_schema_version,
                            commit_sha=spec.commit_sha,
                            dataset_versions=list(versions),
                        )
                    )
                    jobs.append(
                        Job(
                            id=job_id,
                            project_id=spec.project_id,
                            run_id=run_id,
                            job_type="generation_run",
                            idempotency_key=f"run:{run_id}",
                            status="queued",
                            attempt_count=0,
                            max_attempts=3,
                        )
                    )
                    outbox_messages.append(
                        JobOutbox(
                            job_id=job_id,
                            queue_name="generation",
                            payload={"task": "run_generation", "run_id": str(run_id)},
                            status="pending",
                        )
                    )
                    run_ids.append(run_id)
                # UUID-only foreign keys do not tell the ORM the insert dependency
                # graph. Explicit flush boundaries enforce PostgreSQL FK order
                # without committing any partially constructed batch.
                session.flush()  # runs and their immutable dataset-version refs
                session.add_all(jobs)
                session.flush()  # job.run_id -> persisted GenerationRun
                session.add_all(outbox_messages)
                session.flush()  # outbox.job_id -> persisted Job
                batch_id = self._seal(
                    session,
                    project_id=spec.project_id,
                    run_ids=tuple(run_ids),
                    concurrency_limit=spec.concurrency_limit,
                )
                return ScenarioMatrixCreation(batch_id=batch_id, run_ids=tuple(run_ids))

    @staticmethod
    def _seal(
        session: Session,
        *,
        project_id: uuid.UUID,
        run_ids: tuple[uuid.UUID, ...],
        concurrency_limit: int,
    ) -> uuid.UUID:
        """One final transition/trigger path for manual and matrix creation."""

        batch = ScenarioBatch(
            id=uuid.uuid4(),
            project_id=project_id,
            status="draft",
            concurrency_limit=concurrency_limit,
        )
        session.add(batch)
        session.flush()
        session.add_all(
            ScenarioBatchRun(batch_id=batch.id, run_id=run_id, position=position)
            for position, run_id in enumerate(run_ids)
        )
        session.flush()
        batch.status = "queued"
        return batch.id

    def refresh(self, *, batch_id: uuid.UUID) -> ScenarioBatchSnapshot:
        """Recompute parent status under one batch lock from persisted child states."""

        if not isinstance(batch_id, uuid.UUID):
            raise ScenarioBatchError("batch_id must be UUID")
        with self._session_factory() as session:
            with session.begin():
                batch = session.scalar(
                    select(ScenarioBatch)
                    .where(ScenarioBatch.id == batch_id)
                    .with_for_update()
                )
                if batch is None:
                    raise ScenarioBatchError("scenario batch does not exist")
                child_rows = session.execute(
                    select(ScenarioBatchRun.run_id, GenerationRun.status)
                    .join(GenerationRun, GenerationRun.id == ScenarioBatchRun.run_id)
                    .where(ScenarioBatchRun.batch_id == batch_id)
                    .order_by(ScenarioBatchRun.position)
                ).all()
                run_ids = tuple(row.run_id for row in child_rows)
                statuses = tuple(row.status for row in child_rows)
                active_runs = statuses.count("running")
                if batch.status != "draft" and batch.status not in _TERMINAL:
                    if statuses and all(status in _TERMINAL for status in statuses):
                        batch.status = (
                            "failed" if "failed" in statuses
                            else "cancelled" if "cancelled" in statuses
                            else "succeeded"
                        )
                        batch.finished_at = self._clock()
                    elif any(status != "queued" for status in statuses):
                        if batch.status == "queued":
                            batch.status = "running"
                            batch.started_at = self._clock()
                session.flush()
                return ScenarioBatchSnapshot(
                    batch_id=batch.id,
                    project_id=batch.project_id,
                    status=batch.status,
                    concurrency_limit=batch.concurrency_limit,
                    run_ids=run_ids,
                    active_runs=active_runs,
                )


def locked_batch_for_run(session: Session, *, run_id: uuid.UUID) -> ScenarioBatch | None:
    """Lock the parent *before* the generation row to serialize concurrent claims."""

    return session.scalar(
        select(ScenarioBatch)
        .join(ScenarioBatchRun, ScenarioBatchRun.batch_id == ScenarioBatch.id)
        .where(ScenarioBatchRun.run_id == run_id)
        .with_for_update(of=ScenarioBatch)
    )


def require_batch_capacity(session: Session, *, batch: ScenarioBatch) -> None:
    """Require a free persisted slot while caller holds the parent row lock."""

    if batch.status not in {"queued", "running"}:
        raise ScenarioBatchError("batch does not accept new running children")
    active = session.scalar(
        select(func.count())
        .select_from(ScenarioBatchRun)
        .join(GenerationRun, GenerationRun.id == ScenarioBatchRun.run_id)
        .where(
            ScenarioBatchRun.batch_id == batch.id,
            GenerationRun.status == "running",
        )
    )
    if active is None or active >= batch.concurrency_limit:
        from backend.app.application.generation import GenerationRetryScheduled

        raise GenerationRetryScheduled(BATCH_CAPACITY_RETRY_SECONDS)
