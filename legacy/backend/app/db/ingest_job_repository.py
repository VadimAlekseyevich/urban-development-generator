from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.application.ingest import (
    INGEST_JOB_TYPE,
    IngestClaim,
    IngestClaimDisposition,
    IngestJobContext,
    IngestPipelineResult,
    ingest_idempotency_key,
)
from backend.app.db.session import SessionLocal
from backend.app.models.artifact import Artifact, ArtifactLifecycleError, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.job import Job
from backend.app.models.project import Project
from core.urban_generator.domain import ArtifactStat, ArtifactState
from core.urban_generator.domain.errors import (
    DataError,
    PermanentError,
    TransientError,
    UrbanGeneratorError,
)

_DEFAULT_STALE_AFTER = timedelta(minutes=70)
_DATASET_OWNER_TYPE = "dataset_version"


class SqlAlchemyIngestJobRepository:
    """Transactional lifecycle adapter for S03-T09 ingest attempts."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        stale_after: timedelta = _DEFAULT_STALE_AFTER,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        self._session_factory = session_factory
        self._stale_after = stale_after
        self._clock = clock or (lambda: datetime.now(UTC))

    def claim(
        self,
        *,
        job_id: uuid.UUID,
        dataset_version_id: uuid.UUID,
    ) -> IngestClaim:
        if not isinstance(job_id, uuid.UUID) or not isinstance(dataset_version_id, uuid.UUID):
            raise TypeError("job_id and dataset_version_id must be UUID values")
        now = self._clock()
        try:
            with self._session_factory() as session:
                with session.begin():
                    job = self._load_job(session, job_id)
                    version = self._load_dataset_version(session, dataset_version_id)
                    dataset = self._load_dataset(session, version.dataset_id)
                    project = self._load_project(session, dataset.project_id)
                    self._validate_job_binding(
                        job,
                        dataset_version_id=dataset_version_id,
                        project_id=project.id,
                    )

                    if version.status == "ready":
                        self._mark_job_succeeded(job, now=now)
                        return IngestClaim(
                            disposition=IngestClaimDisposition.ALREADY_READY,
                            attempt_count=job.attempt_count,
                            max_attempts=job.max_attempts,
                        )

                    if job.status == "cancelled":
                        return IngestClaim(
                            disposition=IngestClaimDisposition.EXHAUSTED,
                            attempt_count=job.attempt_count,
                            max_attempts=job.max_attempts,
                        )

                    if job.status == "succeeded":
                        raise PermanentError(
                            "ingest job is succeeded but DatasetVersion is not ready",
                            details={"dataset_version_id": str(dataset_version_id)},
                        )

                    if self._is_active_attempt(job, now=now):
                        return IngestClaim(
                            disposition=IngestClaimDisposition.IN_PROGRESS,
                            attempt_count=job.attempt_count,
                            max_attempts=job.max_attempts,
                        )

                    if job.attempt_count >= job.max_attempts:
                        version.status = "failed"
                        if job.status != "failed":
                            job.record_failure(PermanentError("ingest job attempts are exhausted"))
                            job.finished_at = now
                        return IngestClaim(
                            disposition=IngestClaimDisposition.EXHAUSTED,
                            attempt_count=job.attempt_count,
                            max_attempts=job.max_attempts,
                        )

                    if version.status not in {"uploaded", "processing", "failed"}:
                        raise DataError(
                            f"unsupported DatasetVersion ingest status: {version.status!r}",
                            details={"dataset_version_id": str(dataset_version_id)},
                        )

                    job.status = "running"
                    job.attempt_count += 1
                    job.error_class = None
                    job.error_code = None
                    job.error_json = None
                    job.started_at = now
                    job.finished_at = None
                    version.status = "processing"

                    context = IngestJobContext(
                        job_id=job.id,
                        dataset_version_id=version.id,
                        project_id=project.id,
                        dataset_kind=dataset.kind,
                        checksum_sha256=version.checksum_sha256,
                        source_metadata=dict(version.source_metadata),
                        working_srid=project.working_srid,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                    )
                    return IngestClaim(
                        disposition=IngestClaimDisposition.STARTED,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                        context=context,
                    )
        except UrbanGeneratorError:
            raise
        except SQLAlchemyError as exc:
            raise TransientError(
                "unable to claim ingest job from database",
                details={"operation": "claim"},
            ) from exc

    def complete(
        self,
        *,
        context: IngestJobContext,
        result: IngestPipelineResult,
    ) -> None:
        now = self._clock()
        try:
            with self._session_factory() as session:
                with session.begin():
                    job = self._load_job(session, context.job_id)
                    version = self._load_dataset_version(session, context.dataset_version_id)
                    if version.status == "ready":
                        self._mark_job_succeeded(job, now=now)
                        return
                    if version.status != "processing":
                        raise PermanentError(
                            "DatasetVersion must be processing before ingest completion",
                            details={"status": version.status},
                        )
                    if job.status != "running":
                        raise PermanentError(
                            "ingest job must be running before completion",
                            details={"status": job.status},
                        )

                    self._reference_source_artifact(
                        session,
                        stat=result.source_artifact,
                        dataset_version_id=version.id,
                    )
                    for stat in result.derived_artifacts:
                        self._reference_derived_artifact(
                            session,
                            stat=stat,
                            dataset_version_id=version.id,
                        )

                    version.status = "ready"
                    self._mark_job_succeeded(job, now=now)
        except UrbanGeneratorError:
            raise
        except ArtifactLifecycleError as exc:
            raise PermanentError(
                "artifact lifecycle is inconsistent during ingest completion",
                details={"operation": "complete"},
            ) from exc
        except SQLAlchemyError as exc:
            raise TransientError(
                "unable to complete ingest job in database",
                details={"operation": "complete"},
            ) from exc

    def fail(
        self,
        *,
        context: IngestJobContext,
        error: UrbanGeneratorError,
    ) -> bool:
        now = self._clock()
        try:
            with self._session_factory() as session:
                with session.begin():
                    job = self._load_job(session, context.job_id)
                    version = self._load_dataset_version(session, context.dataset_version_id)
                    if version.status == "ready":
                        self._mark_job_succeeded(job, now=now)
                        return False

                    if version.status not in {"uploaded", "processing", "failed"}:
                        raise PermanentError(
                            "cannot fail DatasetVersion from unsupported ingest status",
                            details={"status": version.status},
                        )
                    version.status = "failed"
                    job.record_failure(error)
                    job.finished_at = now
                    return bool(error.retryable and job.attempt_count < job.max_attempts)
        except UrbanGeneratorError:
            raise
        except SQLAlchemyError as exc:
            raise TransientError(
                "unable to persist ingest job failure",
                details={"operation": "fail"},
            ) from exc

    @staticmethod
    def _load_job(session: Session, job_id: uuid.UUID) -> Job:
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            raise PermanentError("ingest job does not exist", details={"job_id": str(job_id)})
        return job

    @staticmethod
    def _load_dataset_version(
        session: Session,
        dataset_version_id: uuid.UUID,
    ) -> DatasetVersion:
        version = session.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.id == dataset_version_id)
            .with_for_update()
        )
        if version is None:
            raise PermanentError(
                "DatasetVersion does not exist",
                details={"dataset_version_id": str(dataset_version_id)},
            )
        return version

    @staticmethod
    def _load_dataset(session: Session, dataset_id: uuid.UUID) -> Dataset:
        dataset = session.scalar(select(Dataset).where(Dataset.id == dataset_id))
        if dataset is None:
            raise PermanentError("Dataset does not exist", details={"dataset_id": str(dataset_id)})
        return dataset

    @staticmethod
    def _load_project(session: Session, project_id: uuid.UUID) -> Project:
        project = session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise PermanentError("Project does not exist", details={"project_id": str(project_id)})
        return project

    @staticmethod
    def _validate_job_binding(
        job: Job,
        *,
        dataset_version_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> None:
        if job.project_id != project_id:
            raise PermanentError(
                "ingest job project does not own DatasetVersion",
                details={"job_id": str(job.id)},
            )
        if job.job_type != INGEST_JOB_TYPE:
            raise PermanentError(
                "unexpected job_type for ingest worker",
                details={"job_type": job.job_type},
            )
        expected_key = ingest_idempotency_key(dataset_version_id)
        if job.idempotency_key != expected_key:
            raise PermanentError(
                "ingest job idempotency key does not match DatasetVersion",
                details={"expected": expected_key},
            )

    def _is_active_attempt(self, job: Job, *, now: datetime) -> bool:
        if job.status != "running" or job.started_at is None:
            return False
        return job.started_at > now - self._stale_after

    @staticmethod
    def _mark_job_succeeded(job: Job, *, now: datetime) -> None:
        job.status = "succeeded"
        job.error_class = None
        job.error_code = None
        job.error_json = None
        job.finished_at = now

    def _reference_source_artifact(
        self,
        session: Session,
        *,
        stat: ArtifactStat,
        dataset_version_id: uuid.UUID,
    ) -> None:
        self._require_ready_stat(stat)
        artifact = session.scalar(
            select(Artifact)
            .where(Artifact.uri == self._artifact_uri(stat))
            .with_for_update()
        )
        if artifact is None:
            raise DataError(
                "source artifact metadata does not exist in database",
                details={"artifact_key": stat.ref.key},
            )
        self._verify_artifact_content(artifact, stat)
        self._reference_existing_artifact(artifact, dataset_version_id=dataset_version_id)

    def _reference_derived_artifact(
        self,
        session: Session,
        *,
        stat: ArtifactStat,
        dataset_version_id: uuid.UUID,
    ) -> None:
        self._require_ready_stat(stat)
        uri = self._artifact_uri(stat)
        artifact = session.scalar(
            select(Artifact).where(Artifact.uri == uri).with_for_update()
        )
        if artifact is None:
            artifact = Artifact(
                uri=uri,
                checksum=stat.checksum,
                size_bytes=stat.size_bytes,
                content_type=stat.content_type,
                state=ArtifactLifecycleState.REFERENCED.value,
                owner_type=_DATASET_OWNER_TYPE,
                owner_id=dataset_version_id,
            )
            session.add(artifact)
            return
        self._verify_artifact_content(artifact, stat)
        self._reference_existing_artifact(artifact, dataset_version_id=dataset_version_id)

    @staticmethod
    def _reference_existing_artifact(
        artifact: Artifact,
        *,
        dataset_version_id: uuid.UUID,
    ) -> None:
        state = ArtifactLifecycleState(artifact.state)
        if state is ArtifactLifecycleState.READY:
            artifact.transition_to(
                ArtifactLifecycleState.REFERENCED,
                owner_type=_DATASET_OWNER_TYPE,
                owner_id=dataset_version_id,
            )
            return
        if state is ArtifactLifecycleState.REFERENCED:
            if (
                artifact.owner_type != _DATASET_OWNER_TYPE
                or artifact.owner_id != dataset_version_id
            ):
                raise PermanentError(
                    "artifact is already referenced by another owner",
                    details={"artifact_id": str(artifact.id)},
                )
            return
        raise PermanentError(
            "artifact is not referenceable from its current lifecycle state",
            details={"state": state.value},
        )

    @staticmethod
    def _verify_artifact_content(artifact: Artifact, stat: ArtifactStat) -> None:
        if (
            artifact.checksum != stat.checksum
            or artifact.size_bytes != stat.size_bytes
            or artifact.content_type != stat.content_type
        ):
            raise DataError(
                "artifact database metadata does not match ArtifactStore",
                details={"artifact_key": stat.ref.key},
            )

    @staticmethod
    def _require_ready_stat(stat: ArtifactStat) -> None:
        if stat.ref.state is not ArtifactState.READY:
            raise PermanentError(
                "completed ingest artifacts must be ready",
                details={"artifact_key": stat.ref.key},
            )

    @staticmethod
    def _artifact_uri(stat: ArtifactStat) -> str:
        return f"artifact://{stat.ref.key}"
