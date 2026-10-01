from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.geojson_exports import (
    GEOJSON_EXPORT_CONTENT_TYPE,
    GEOJSON_EXPORT_JOB_TYPE,
    GeoJsonExportArtifactSnapshot,
    GeoJsonExportClaim,
    GeoJsonExportClaimDisposition,
    GeoJsonExportConflictError,
    GeoJsonExportJobContext,
    GeoJsonExportNotFoundError,
    GeoJsonExportPipelineResult,
    GeoJsonExportSnapshot,
    GeoJsonExportSpec,
    geojson_export_filename,
)
from backend.app.application.layer_catalog import layer_catalog_for_context
from backend.app.db.session import SessionLocal
from backend.app.db.vector_layer_query_repository import SqlAlchemyVectorLayerRepository
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.geojson_export import GeoJsonExport
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from core.urban_generator.domain import ArtifactRef, ArtifactState
from core.urban_generator.domain.errors import UrbanGeneratorError

_DEFAULT_STALE_AFTER = timedelta(minutes=70)
_ARTIFACT_OWNER_TYPE = "job"


class SqlAlchemyGeoJsonExportRepository:
    """DB-authoritative export request/job/outbox lifecycle and artifact ownership."""

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

    def create(
        self,
        *,
        project_id: uuid.UUID,
        spec: GeoJsonExportSpec,
    ) -> GeoJsonExportSnapshot:
        with self._session_factory() as session:
            with session.begin():
                project = session.scalar(
                    select(Project)
                    .where(Project.id == project_id)
                    .with_for_update()
                )
                if project is None:
                    raise GeoJsonExportNotFoundError("project not found")
                self._validate_owner(session, project_id=project_id, spec=spec)

                existing = session.scalar(
                    select(Job).where(
                        Job.project_id == project_id,
                        Job.job_type == GEOJSON_EXPORT_JOB_TYPE,
                        Job.idempotency_key == spec.idempotency_key,
                    )
                )
                if existing is not None:
                    export = session.get(GeoJsonExport, existing.id)
                    if export is None or not _same_spec(export, spec):
                        raise GeoJsonExportConflictError(
                            "export idempotency collision with different request"
                        )
                    return self._snapshot(session, existing, export)

                job = Job(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    run_id=None,
                    job_type=GEOJSON_EXPORT_JOB_TYPE,
                    idempotency_key=spec.idempotency_key,
                    status="queued",
                    attempt_count=0,
                    max_attempts=3,
                )
                session.add(job)
                session.flush()
                export = GeoJsonExport(
                    job_id=job.id,
                    layer_id=spec.layer_id,
                    dataset_version_id=spec.dataset_version_id,
                    run_id=spec.run_id,
                    bbox_west=spec.bbox[0],
                    bbox_south=spec.bbox[1],
                    bbox_east=spec.bbox[2],
                    bbox_north=spec.bbox[3],
                    max_features=spec.max_features,
                    feature_count=None,
                    artifact_id=None,
                )
                session.add(export)
                session.add(
                    JobOutbox(
                        job_id=job.id,
                        queue_name="export",
                        payload={
                            "task": "run_geojson_export",
                            "job_id": str(job.id),
                        },
                        status="pending",
                    )
                )
                session.flush()
                return self._snapshot(session, job, export)

    def get(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> GeoJsonExportSnapshot:
        with self._session_factory() as session:
            job, export = self._load(session, job_id=job_id, lock=False)
            if job.project_id != project_id:
                raise GeoJsonExportNotFoundError("export job not found in project")
            return self._snapshot(session, job, export)

    def ready_artifact_ref(
        self,
        *,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> tuple[ArtifactRef, str, int]:
        with self._session_factory() as session:
            job, export = self._load(session, job_id=job_id, lock=False)
            if job.project_id != project_id:
                raise GeoJsonExportNotFoundError("export job not found in project")
            if job.status != "succeeded" or export.artifact_id is None:
                raise GeoJsonExportConflictError("export artifact is not ready")
            artifact = session.get(Artifact, export.artifact_id)
            if (
                artifact is None
                or artifact.state != ArtifactLifecycleState.REFERENCED.value
                or artifact.owner_type != _ARTIFACT_OWNER_TYPE
                or artifact.owner_id != job.id
            ):
                raise GeoJsonExportConflictError("export artifact ownership is inconsistent")
            key = _artifact_key(artifact.uri)
            return (
                ArtifactRef(key, ArtifactState.READY),
                geojson_export_filename(export.layer_id, job.id),
                artifact.size_bytes,
            )

    def claim(self, *, job_id: uuid.UUID) -> GeoJsonExportClaim:
        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                job, export = self._load(session, job_id=job_id, lock=True)
                if job.job_type != GEOJSON_EXPORT_JOB_TYPE:
                    raise GeoJsonExportNotFoundError("job is not a GeoJSON export")
                if job.status == "succeeded":
                    if export.artifact_id is None:
                        raise GeoJsonExportConflictError(
                            "succeeded export job has no artifact"
                        )
                    return GeoJsonExportClaim(
                        disposition=GeoJsonExportClaimDisposition.ALREADY_SUCCEEDED,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                    )
                if job.status == "cancelled" or job.attempt_count >= job.max_attempts:
                    return GeoJsonExportClaim(
                        disposition=GeoJsonExportClaimDisposition.EXHAUSTED,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                    )
                if (
                    job.status == "running"
                    and job.started_at is not None
                    and now - job.started_at < self._stale_after
                ):
                    return GeoJsonExportClaim(
                        disposition=GeoJsonExportClaimDisposition.IN_PROGRESS,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                    )
                if job.status not in {"queued", "failed", "running"}:
                    raise GeoJsonExportConflictError(
                        f"unsupported export job status: {job.status}"
                    )

                job.status = "running"
                job.attempt_count += 1
                job.error_class = None
                job.error_code = None
                job.error_json = None
                job.started_at = now
                job.finished_at = None
                context = GeoJsonExportJobContext(
                    job_id=job.id,
                    project_id=job.project_id,
                    spec=_spec(export),
                    attempt_count=job.attempt_count,
                    max_attempts=job.max_attempts,
                )
                return GeoJsonExportClaim(
                    disposition=GeoJsonExportClaimDisposition.STARTED,
                    attempt_count=job.attempt_count,
                    max_attempts=job.max_attempts,
                    context=context,
                )

    def complete(
        self,
        *,
        context: GeoJsonExportJobContext,
        result: GeoJsonExportPipelineResult,
    ) -> None:
        expected_key = (
            f"exports/{context.project_id}/{context.job_id}/"
            f"{geojson_export_filename(context.spec.layer_id, context.job_id)}"
        )
        stat = result.artifact
        if stat.ref.state is not ArtifactState.READY or stat.ref.key != expected_key:
            raise GeoJsonExportConflictError("export pipeline returned unexpected artifact")
        if stat.content_type != GEOJSON_EXPORT_CONTENT_TYPE:
            raise GeoJsonExportConflictError("export artifact content type is invalid")
        if not 0 <= result.feature_count <= context.spec.max_features:
            raise GeoJsonExportConflictError("export feature count is outside request cap")

        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                job, export = self._load(session, job_id=context.job_id, lock=True)
                self._require_attempt(job, context)
                if not _same_spec(export, context.spec):
                    raise GeoJsonExportConflictError("persisted export request changed")

                uri = f"artifact://{stat.ref.key}"
                artifact = session.scalar(select(Artifact).where(Artifact.uri == uri))
                if artifact is None:
                    artifact = Artifact(
                        uri=uri,
                        checksum=stat.checksum,
                        size_bytes=stat.size_bytes,
                        content_type=stat.content_type,
                        state=ArtifactLifecycleState.TEMPORARY.value,
                        owner_type=None,
                        owner_id=None,
                    )
                    session.add(artifact)
                    session.flush()
                    artifact.transition_to(ArtifactLifecycleState.READY)
                    session.flush()
                    artifact.transition_to(
                        ArtifactLifecycleState.REFERENCED,
                        owner_type=_ARTIFACT_OWNER_TYPE,
                        owner_id=job.id,
                    )
                    session.flush()
                else:
                    if (
                        artifact.checksum != stat.checksum
                        or artifact.size_bytes != stat.size_bytes
                        or artifact.content_type != stat.content_type
                        or artifact.state != ArtifactLifecycleState.REFERENCED.value
                        or artifact.owner_type != _ARTIFACT_OWNER_TYPE
                        or artifact.owner_id != job.id
                    ):
                        raise GeoJsonExportConflictError(
                            "existing artifact metadata does not match export"
                        )

                export.artifact_id = artifact.id
                export.feature_count = result.feature_count
                job.status = "succeeded"
                job.error_class = None
                job.error_code = None
                job.error_json = None
                job.finished_at = now

    def fail(
        self,
        *,
        context: GeoJsonExportJobContext,
        error: UrbanGeneratorError,
    ) -> bool:
        if not isinstance(error, UrbanGeneratorError):
            raise TypeError("error must belong to the canonical error taxonomy")
        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                job, _export = self._load(session, job_id=context.job_id, lock=True)
                self._require_attempt(job, context)
                job.record_failure(error)
                job.finished_at = now
                return error.retryable and job.attempt_count < job.max_attempts

    def _validate_owner(
        self,
        session: Session,
        *,
        project_id: uuid.UUID,
        spec: GeoJsonExportSpec,
    ) -> None:
        if spec.dataset_version_id is not None:
            row = session.execute(
                select(DatasetVersion.status)
                .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                .where(
                    DatasetVersion.id == spec.dataset_version_id,
                    Dataset.project_id == project_id,
                )
            ).one_or_none()
            if row is None:
                raise GeoJsonExportNotFoundError(
                    "dataset version not found in project"
                )
            if row[0] != "ready":
                raise GeoJsonExportConflictError(
                    "GeoJSON export requires a ready DatasetVersion"
                )
        else:
            run = session.scalar(
                select(GenerationRun).where(
                    GenerationRun.id == spec.run_id,
                    GenerationRun.project_id == project_id,
                )
            )
            if run is None:
                raise GeoJsonExportNotFoundError("run not found in project")
            if run.status != "succeeded":
                raise GeoJsonExportConflictError(
                    "GeoJSON export requires a succeeded run"
                )

        entry = layer_catalog_for_context(
            project_id=project_id,
            dataset_version_id=spec.dataset_version_id,
            run_id=spec.run_id,
        ).get(spec.layer_id)
        if entry is None:
            raise GeoJsonExportConflictError("layer owner does not match export request")
        context = SqlAlchemyVectorLayerRepository(session).get_context(entry)
        if context is None:
            raise GeoJsonExportNotFoundError("layer owner not found in project")
        if not context.read_model_ready:
            raise GeoJsonExportConflictError(
                "run read model is not published for this layer"
            )

    @staticmethod
    def _load(
        session: Session,
        *,
        job_id: uuid.UUID,
        lock: bool,
    ) -> tuple[Job, GeoJsonExport]:
        statement = (
            select(Job, GeoJsonExport)
            .join(GeoJsonExport, GeoJsonExport.job_id == Job.id)
            .where(Job.id == job_id, Job.job_type == GEOJSON_EXPORT_JOB_TYPE)
        )
        if lock:
            statement = statement.with_for_update(of=(Job, GeoJsonExport))
        row = session.execute(statement).one_or_none()
        if row is None:
            raise GeoJsonExportNotFoundError("GeoJSON export job not found")
        return row[0], row[1]

    @staticmethod
    def _snapshot(
        session: Session,
        job: Job,
        export: GeoJsonExport,
    ) -> GeoJsonExportSnapshot:
        artifact_snapshot: GeoJsonExportArtifactSnapshot | None = None
        if export.artifact_id is not None:
            artifact = session.get(Artifact, export.artifact_id)
            if artifact is None:
                raise GeoJsonExportConflictError("export artifact metadata is missing")
            artifact_snapshot = GeoJsonExportArtifactSnapshot(
                artifact_id=artifact.id,
                filename=geojson_export_filename(export.layer_id, job.id),
                size_bytes=artifact.size_bytes,
                checksum=artifact.checksum,
                content_type=artifact.content_type or GEOJSON_EXPORT_CONTENT_TYPE,
            )
        return GeoJsonExportSnapshot(
            job_id=job.id,
            project_id=job.project_id,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            layer_id=export.layer_id,
            dataset_version_id=export.dataset_version_id,
            run_id=export.run_id,
            bbox=export.bbox,
            max_features=export.max_features,
            artifact=artifact_snapshot,
            feature_count=export.feature_count,
            error_class=job.error_class,
            error_code=job.error_code,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    @staticmethod
    def _require_attempt(job: Job, context: GeoJsonExportJobContext) -> None:
        if (
            job.status != "running"
            or job.project_id != context.project_id
            or job.attempt_count != context.attempt_count
        ):
            raise GeoJsonExportConflictError("export job attempt is no longer current")


def _spec(export: GeoJsonExport) -> GeoJsonExportSpec:
    return GeoJsonExportSpec(
        layer_id=export.layer_id,
        dataset_version_id=export.dataset_version_id,
        run_id=export.run_id,
        bbox=export.bbox,
        max_features=export.max_features,
    )


def _same_spec(export: GeoJsonExport, spec: GeoJsonExportSpec) -> bool:
    return _spec(export) == spec


def _artifact_key(uri: str) -> str:
    prefix = "artifact://"
    if not uri.startswith(prefix) or len(uri) <= len(prefix):
        raise GeoJsonExportConflictError("export artifact URI is invalid")
    return uri[len(prefix):]
