"""Atomic project-scoped Artifact -> DatasetVersion -> Job -> Outbox submission.

The existing ingest worker is the sole authority that marks a version ready.
The uploaded blob stays READY until the worker references it on success.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.ingest import INGEST_JOB_TYPE, ingest_idempotency_key
from backend.app.db.job_admission import enforce_project_job_capacity
from backend.app.db.session import SessionLocal
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project

IngestFormat = Literal["geojson", "gpkg", "shapefile_zip", "geotiff"]
IngestKind = Literal[
    "roads", "buildings", "landuse", "water", "facilities", "constraints", "dem"
]


class IngestSubmissionNotFound(ValueError):
    pass


class IngestSubmissionConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IngestSubmission:
    project_id: uuid.UUID
    dataset_version_id: uuid.UUID
    dataset_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    job_status: str
    attempt_count: int
    max_attempts: int
    retry_pending: bool
    error_code: str | None
    error_message: str | None


class SqlAlchemyIngestSubmissionService:
    """Create one immutable source version and DB-authoritative queued ingest job."""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _submission(
        *,
        project_id: uuid.UUID,
        version: DatasetVersion,
        job: Job,
    ) -> IngestSubmission:
        return IngestSubmission(
            project_id=project_id,
            dataset_version_id=version.id,
            dataset_id=version.dataset_id,
            job_id=job.id,
            status=version.status,
            job_status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            retry_pending=(
                job.status == "failed"
                and job.attempt_count < job.max_attempts
                and isinstance(job.error_json, dict)
                and job.error_json.get("retryable") is True
            ),
            error_code=job.error_code,
            error_message=(
                str(job.error_json["message"])
                if isinstance(job.error_json, dict)
                and isinstance(job.error_json.get("message"), str)
                else None
            ),
        )

    @staticmethod
    def _job(session: Session, *, project_id: uuid.UUID, version_id: uuid.UUID) -> Job:
        job = session.scalar(
            select(Job).where(
                Job.project_id == project_id,
                Job.job_type == INGEST_JOB_TYPE,
                Job.idempotency_key == ingest_idempotency_key(version_id),
            )
        )
        if job is None:
            raise IngestSubmissionConflict("dataset version has no authoritative ingest job")
        return job

    def create(
        self,
        *,
        project_id: uuid.UUID,
        artifact_id: uuid.UUID,
        kind: IngestKind,
        source_format: IngestFormat,
        layer: str | None = None,
    ) -> IngestSubmission:
        if kind == "dem" and source_format != "geotiff":
            raise IngestSubmissionConflict("DEM requires GeoTIFF format")
        if kind != "dem" and source_format == "geotiff":
            raise IngestSubmissionConflict("GeoTIFF requires DEM dataset kind")
        if source_format != "gpkg" and layer is not None:
            raise IngestSubmissionConflict("named source layer is only valid for GeoPackage")
        with self._session_factory() as session:
            with session.begin():
                project = session.scalar(
                    select(Project).where(Project.id == project_id).with_for_update()
                )
                if project is None:
                    raise IngestSubmissionNotFound("project not found")
                artifact = session.scalar(
                    select(Artifact).where(Artifact.id == artifact_id).with_for_update()
                )
                if artifact is None:
                    raise IngestSubmissionNotFound("upload artifact not found")
                if not artifact.uri.startswith("artifact://uploads/"):
                    raise IngestSubmissionConflict("only uploaded artifacts may be imported")
                if artifact.state not in (
                    ArtifactLifecycleState.READY.value,
                    ArtifactLifecycleState.REFERENCED.value,
                ):
                    raise IngestSubmissionConflict("artifact must be ready for ingest")

                artifact_key = artifact.uri.removeprefix("artifact://")
                existing = session.execute(
                    select(DatasetVersion, Dataset)
                    .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                    .where(DatasetVersion.source_metadata["artifact_key"].astext == artifact_key)
                    .with_for_update(of=DatasetVersion)
                ).one_or_none()
                if existing is not None:
                    version, dataset = existing
                    if (
                        dataset.project_id != project_id
                        or dataset.kind != kind
                        or version.source_metadata.get("format") != source_format
                        or version.source_metadata.get("layer") != layer
                    ):
                        raise IngestSubmissionConflict(
                            "uploaded artifact is already attached to another ingest"
                        )
                    return self._submission(
                        project_id=project_id, version=version,
                        job=self._job(session, project_id=project_id, version_id=version.id),
                    )
                if artifact.state != ArtifactLifecycleState.READY.value:
                    raise IngestSubmissionConflict("artifact is already referenced")

                enforce_project_job_capacity(
                    session, project_id=project_id, job_type=INGEST_JOB_TYPE
                )
                dataset_id = uuid.uuid4()
                version_id = uuid.uuid4()
                job_id = uuid.uuid4()
                dataset = Dataset(id=dataset_id, project_id=project_id, kind=kind)
                version = DatasetVersion(
                    id=version_id,
                    dataset_id=dataset_id,
                    version=1,
                    status="uploaded",
                    checksum_sha256=artifact.checksum.removeprefix("sha256:"),
                    source_metadata={
                        "artifact_key": artifact_key,
                        "format": source_format,
                        **({"layer": layer} if layer is not None else {}),
                    },
                )
                job = Job(
                    id=job_id,
                    project_id=project_id,
                    job_type=INGEST_JOB_TYPE,
                    idempotency_key=ingest_idempotency_key(version_id),
                    status="queued",
                    attempt_count=0,
                    max_attempts=3,
                )
                outbox = JobOutbox(
                    job_id=job_id,
                    queue_name="ingest",
                    payload={
                        "task": "run_ingest",
                        "job_id": str(job_id),
                        "dataset_version_id": str(version_id),
                    },
                    status="pending",
                )
                session.add_all((dataset, version, job, outbox))
                session.flush()
                return self._submission(project_id=project_id, version=version, job=job)

    def get(self, *, project_id: uuid.UUID, version_id: uuid.UUID) -> IngestSubmission:
        with self._session_factory() as session:
            version = session.scalar(
                select(DatasetVersion)
                .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                .where(
                    DatasetVersion.id == version_id,
                    Dataset.project_id == project_id,
                )
            )
            if version is None:
                raise IngestSubmissionNotFound("ingest dataset version not found in project")
            return self._submission(
                project_id=project_id, version=version,
                job=self._job(session, project_id=project_id, version_id=version.id),
            )
