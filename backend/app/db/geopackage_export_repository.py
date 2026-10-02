from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.geopackage_exports import (
    GEOPACKAGE_EXPORT_CONTENT_TYPE,
    GEOPACKAGE_EXPORT_JOB_TYPE,
    GeoPackageExportArtifactSnapshot,
    GeoPackageExportClaim,
    GeoPackageExportClaimDisposition,
    GeoPackageExportConflictError,
    GeoPackageExportJobContext,
    GeoPackageExportNotFoundError,
    GeoPackageExportPipelineResult,
    GeoPackageExportSnapshot,
    GeoPackageExportSpec,
    GeoPackageLayerCount,
    geopackage_export_filename,
)
from backend.app.application.layer_catalog import layer_catalog_for_context
from backend.app.db.session import SessionLocal
from backend.app.db.vector_layer_query_repository import SqlAlchemyVectorLayerRepository
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.geopackage_export import GeoPackageExport
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from core.urban_generator.domain import ArtifactRef, ArtifactState
from core.urban_generator.domain.errors import UrbanGeneratorError

_DEFAULT_STALE_AFTER = timedelta(minutes=70)
_ARTIFACT_OWNER_TYPE = "job"


class SqlAlchemyGeoPackageExportRepository:
    """DB-authoritative multi-layer export lifecycle and artifact ownership."""

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
        spec: GeoPackageExportSpec,
    ) -> GeoPackageExportSnapshot:
        with self._session_factory() as session:
            with session.begin():
                project = session.scalar(
                    select(Project)
                    .where(Project.id == project_id)
                    .with_for_update()
                )
                if project is None:
                    raise GeoPackageExportNotFoundError("project not found")
                self._validate_owners_and_layers(
                    session,
                    project_id=project_id,
                    spec=spec,
                )

                existing = session.scalar(
                    select(Job).where(
                        Job.project_id == project_id,
                        Job.job_type == GEOPACKAGE_EXPORT_JOB_TYPE,
                        Job.idempotency_key == spec.idempotency_key,
                    )
                )
                if existing is not None:
                    export = session.get(GeoPackageExport, existing.id)
                    if export is None or not _same_spec(export, spec):
                        raise GeoPackageExportConflictError(
                            "export idempotency collision with different request"
                        )
                    return self._snapshot(session, existing, export)

                job = Job(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    run_id=None,
                    job_type=GEOPACKAGE_EXPORT_JOB_TYPE,
                    idempotency_key=spec.idempotency_key,
                    status="queued",
                    attempt_count=0,
                    max_attempts=3,
                )
                session.add(job)
                session.flush()
                export = GeoPackageExport(
                    job_id=job.id,
                    layer_ids_json=list(spec.layer_ids),
                    dataset_version_id=spec.dataset_version_id,
                    run_id=spec.run_id,
                    bbox_west=spec.bbox[0],
                    bbox_south=spec.bbox[1],
                    bbox_east=spec.bbox[2],
                    bbox_north=spec.bbox[3],
                    max_features_per_layer=spec.max_features_per_layer,
                    max_total_features=spec.max_total_features,
                    layer_feature_counts_json=None,
                    artifact_id=None,
                )
                session.add(export)
                session.add(
                    JobOutbox(
                        job_id=job.id,
                        queue_name="export",
                        payload={
                            "task": "run_geopackage_export",
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
    ) -> GeoPackageExportSnapshot:
        with self._session_factory() as session:
            job, export = self._load(session, job_id=job_id, lock=False)
            if job.project_id != project_id:
                raise GeoPackageExportNotFoundError("export job not found in project")
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
                raise GeoPackageExportNotFoundError("export job not found in project")
            if job.status != "succeeded" or export.artifact_id is None:
                raise GeoPackageExportConflictError("export artifact is not ready")
            artifact = session.get(Artifact, export.artifact_id)
            if (
                artifact is None
                or artifact.state != ArtifactLifecycleState.REFERENCED.value
                or artifact.owner_type != _ARTIFACT_OWNER_TYPE
                or artifact.owner_id != job.id
            ):
                raise GeoPackageExportConflictError(
                    "export artifact ownership is inconsistent"
                )
            return (
                ArtifactRef(_artifact_key(artifact.uri), ArtifactState.READY),
                geopackage_export_filename(job.id),
                artifact.size_bytes,
            )

    def claim(self, *, job_id: uuid.UUID) -> GeoPackageExportClaim:
        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                job, export = self._load(session, job_id=job_id, lock=True)
                if job.job_type != GEOPACKAGE_EXPORT_JOB_TYPE:
                    raise GeoPackageExportNotFoundError(
                        "job is not a GeoPackage export"
                    )
                if job.status == "succeeded":
                    if export.artifact_id is None:
                        raise GeoPackageExportConflictError(
                            "succeeded export job has no artifact"
                        )
                    return GeoPackageExportClaim(
                        disposition=GeoPackageExportClaimDisposition.ALREADY_SUCCEEDED,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                    )
                if job.status == "cancelled" or job.attempt_count >= job.max_attempts:
                    return GeoPackageExportClaim(
                        disposition=GeoPackageExportClaimDisposition.EXHAUSTED,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                    )
                if (
                    job.status == "running"
                    and job.started_at is not None
                    and now - job.started_at < self._stale_after
                ):
                    return GeoPackageExportClaim(
                        disposition=GeoPackageExportClaimDisposition.IN_PROGRESS,
                        attempt_count=job.attempt_count,
                        max_attempts=job.max_attempts,
                    )
                if job.status not in {"queued", "failed", "running"}:
                    raise GeoPackageExportConflictError(
                        f"unsupported export job status: {job.status}"
                    )

                job.status = "running"
                job.attempt_count += 1
                job.error_class = None
                job.error_code = None
                job.error_json = None
                job.started_at = now
                job.finished_at = None
                context = GeoPackageExportJobContext(
                    job_id=job.id,
                    project_id=job.project_id,
                    spec=_spec(export),
                    attempt_count=job.attempt_count,
                    max_attempts=job.max_attempts,
                )
                return GeoPackageExportClaim(
                    disposition=GeoPackageExportClaimDisposition.STARTED,
                    attempt_count=job.attempt_count,
                    max_attempts=job.max_attempts,
                    context=context,
                )

    def complete(
        self,
        *,
        context: GeoPackageExportJobContext,
        result: GeoPackageExportPipelineResult,
    ) -> None:
        expected_key = (
            f"exports/{context.project_id}/{context.job_id}/"
            f"{geopackage_export_filename(context.job_id)}"
        )
        stat = result.artifact
        if stat.ref.state is not ArtifactState.READY or stat.ref.key != expected_key:
            raise GeoPackageExportConflictError(
                "export pipeline returned unexpected artifact"
            )
        if stat.content_type != GEOPACKAGE_EXPORT_CONTENT_TYPE:
            raise GeoPackageExportConflictError(
                "export artifact content type is invalid"
            )
        if tuple(value.layer_id for value in result.layer_counts) != context.spec.layer_ids:
            raise GeoPackageExportConflictError(
                "export layer counts do not match immutable layer selection"
            )
        if any(
            value.feature_count > context.spec.max_features_per_layer
            for value in result.layer_counts
        ):
            raise GeoPackageExportConflictError(
                "export layer count is outside request cap"
            )
        if result.total_feature_count > context.spec.max_total_features:
            raise GeoPackageExportConflictError(
                "export total feature count is outside request cap"
            )

        now = self._clock()
        with self._session_factory() as session:
            with session.begin():
                job, export = self._load(session, job_id=context.job_id, lock=True)
                self._require_attempt(job, context)
                if not _same_spec(export, context.spec):
                    raise GeoPackageExportConflictError(
                        "persisted export request changed"
                    )

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
                elif (
                    artifact.checksum != stat.checksum
                    or artifact.size_bytes != stat.size_bytes
                    or artifact.content_type != stat.content_type
                    or artifact.state != ArtifactLifecycleState.REFERENCED.value
                    or artifact.owner_type != _ARTIFACT_OWNER_TYPE
                    or artifact.owner_id != job.id
                ):
                    raise GeoPackageExportConflictError(
                        "existing artifact metadata does not match export"
                    )

                export.artifact_id = artifact.id
                export.layer_feature_counts_json = {
                    value.layer_id: value.feature_count
                    for value in result.layer_counts
                }
                job.status = "succeeded"
                job.error_class = None
                job.error_code = None
                job.error_json = None
                job.finished_at = now

    def fail(
        self,
        *,
        context: GeoPackageExportJobContext,
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

    def _validate_owners_and_layers(
        self,
        session: Session,
        *,
        project_id: uuid.UUID,
        spec: GeoPackageExportSpec,
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
                raise GeoPackageExportNotFoundError(
                    "dataset version not found in project"
                )
            if row[0] != "ready":
                raise GeoPackageExportConflictError(
                    "GeoPackage export requires a ready DatasetVersion"
                )

        if spec.run_id is not None:
            run = session.scalar(
                select(GenerationRun).where(
                    GenerationRun.id == spec.run_id,
                    GenerationRun.project_id == project_id,
                )
            )
            if run is None:
                raise GeoPackageExportNotFoundError("run not found in project")
            if run.status != "succeeded":
                raise GeoPackageExportConflictError(
                    "GeoPackage export requires a succeeded run"
                )

        catalog = layer_catalog_for_context(
            project_id=project_id,
            dataset_version_id=spec.dataset_version_id,
            run_id=spec.run_id,
        )
        repository = SqlAlchemyVectorLayerRepository(session)
        for layer_id in spec.layer_ids:
            entry = catalog.get(layer_id)
            if entry is None:
                raise GeoPackageExportConflictError(
                    "layer owner does not match export request"
                )
            context = repository.get_context(entry)
            if context is None:
                raise GeoPackageExportNotFoundError(
                    "layer owner not found in project"
                )
            if not context.read_model_ready:
                raise GeoPackageExportConflictError(
                    f"run read model is not published for {layer_id}"
                )

    @staticmethod
    def _load(
        session: Session,
        *,
        job_id: uuid.UUID,
        lock: bool,
    ) -> tuple[Job, GeoPackageExport]:
        statement = (
            select(Job, GeoPackageExport)
            .join(GeoPackageExport, GeoPackageExport.job_id == Job.id)
            .where(
                Job.id == job_id,
                Job.job_type == GEOPACKAGE_EXPORT_JOB_TYPE,
            )
        )
        if lock:
            statement = statement.with_for_update(of=(Job, GeoPackageExport))
        row = session.execute(statement).one_or_none()
        if row is None:
            raise GeoPackageExportNotFoundError("GeoPackage export job not found")
        return row[0], row[1]

    @staticmethod
    def _snapshot(
        session: Session,
        job: Job,
        export: GeoPackageExport,
    ) -> GeoPackageExportSnapshot:
        artifact_snapshot: GeoPackageExportArtifactSnapshot | None = None
        if export.artifact_id is not None:
            artifact = session.get(Artifact, export.artifact_id)
            if artifact is None:
                raise GeoPackageExportConflictError(
                    "export artifact metadata is missing"
                )
            artifact_snapshot = GeoPackageExportArtifactSnapshot(
                artifact_id=artifact.id,
                filename=geopackage_export_filename(job.id),
                size_bytes=artifact.size_bytes,
                checksum=artifact.checksum,
                content_type=artifact.content_type
                or GEOPACKAGE_EXPORT_CONTENT_TYPE,
            )

        layer_counts: tuple[GeoPackageLayerCount, ...] | None = None
        if export.layer_feature_counts_json is not None:
            counts = export.layer_feature_counts_json
            if set(counts) != set(export.layer_ids_json):
                raise GeoPackageExportConflictError(
                    "persisted GeoPackage feature counts are inconsistent"
                )
            layer_counts = tuple(
                GeoPackageLayerCount(
                    layer_id=layer_id,
                    feature_count=counts[layer_id],
                )
                for layer_id in export.layer_ids_json
            )

        snapshot = GeoPackageExportSnapshot(
            job_id=job.id,
            project_id=job.project_id,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            layer_ids=tuple(export.layer_ids_json),
            dataset_version_id=export.dataset_version_id,
            run_id=export.run_id,
            bbox=export.bbox,
            max_features_per_layer=export.max_features_per_layer,
            max_total_features=export.max_total_features,
            artifact=artifact_snapshot,
            layer_counts=layer_counts,
            error_class=job.error_class,
            error_code=job.error_code,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        if snapshot.total_feature_count is not None and (
            snapshot.total_feature_count > export.max_total_features
        ):
            raise GeoPackageExportConflictError(
                "persisted GeoPackage feature counts exceed request cap"
            )
        return snapshot

    @staticmethod
    def _require_attempt(job: Job, context: GeoPackageExportJobContext) -> None:
        if (
            job.status != "running"
            or job.project_id != context.project_id
            or job.attempt_count != context.attempt_count
        ):
            raise GeoPackageExportConflictError(
                "export job attempt is no longer current"
            )


def _spec(export: GeoPackageExport) -> GeoPackageExportSpec:
    return GeoPackageExportSpec(
        layer_ids=tuple(export.layer_ids_json),
        dataset_version_id=export.dataset_version_id,
        run_id=export.run_id,
        bbox=export.bbox,
        max_features_per_layer=export.max_features_per_layer,
        max_total_features=export.max_total_features,
    )


def _same_spec(export: GeoPackageExport, spec: GeoPackageExportSpec) -> bool:
    return _spec(export) == spec


def _artifact_key(uri: str) -> str:
    prefix = "artifact://"
    if not uri.startswith(prefix) or len(uri) <= len(prefix):
        raise GeoPackageExportConflictError("export artifact URI is invalid")
    return uri[len(prefix):]
