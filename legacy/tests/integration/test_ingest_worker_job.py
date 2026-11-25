import io
import json
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from backend.app.adapters import LocalArtifactStore
from backend.app.application.ingest import (
    INGEST_JOB_TYPE,
    IngestJobRunStatus,
    IngestJobService,
    IngestPipelineResult,
    ingest_idempotency_key,
)
from backend.app.db.ingest_job_repository import SqlAlchemyIngestJobRepository
from backend.app.db.session import engine
from backend.app.db.source_layer_writer import SqlAlchemySourceLayerBatchWriter
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.job import Job
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceRoad
from backend.app.services.ingest_pipeline import DatasetIngestPipeline
from core.urban_generator.domain import ArtifactRef, ArtifactStat
from core.urban_generator.domain.errors import TransientError

WORKING_SRID = 3857
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _migrate_to_head() -> None:
    command.upgrade(Config("alembic.ini"), "head")


def _truncate() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None):
    _truncate()
    yield
    _truncate()


def _roads_geojson() -> bytes:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "r1",
                "properties": {
                    "road_class": "residential",
                    "name": "Alpha",
                    "lanes": 2,
                    "one_way": False,
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[0.0, 0.0], [0.001, 0.001]],
                },
            },
            {
                "type": "Feature",
                "id": "r2",
                "properties": {
                    "road_class": "secondary",
                    "name": "Beta",
                    "one_way": True,
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[0.001, 0.0], [0.002, 0.001]],
                },
            },
        ],
    }
    return json.dumps(payload).encode("utf-8")


def _create_ingest_state(
    store: LocalArtifactStore,
    *,
    source_bytes: bytes,
    source_format: str = "geojson",
    dataset_kind: str = "roads",
    max_attempts: int = 3,
) -> tuple[uuid.UUID, uuid.UUID, ArtifactStat]:
    temporary_ref = ArtifactRef(f"uploads/{uuid.uuid4().hex}/source.geojson")
    store.put(
        temporary_ref,
        io.BytesIO(source_bytes),
        content_type="application/geo+json",
    )
    source_stat = store.promote(temporary_ref)

    with SessionFactory() as session:
        project = Project(
            name="Ingest integration",
            working_srid=WORKING_SRID,
            boundary_metadata={},
        )
        session.add(project)
        session.flush()

        dataset = Dataset(project_id=project.id, kind=dataset_kind)
        session.add(dataset)
        session.flush()

        version = DatasetVersion(
            dataset_id=dataset.id,
            version=1,
            checksum_sha256=source_stat.checksum.removeprefix("sha256:"),
            status="uploaded",
            source_metadata={
                "artifact_key": source_stat.ref.key,
                "format": source_format,
            },
        )
        session.add(version)
        session.flush()

        artifact = Artifact(
            uri=f"artifact://{source_stat.ref.key}",
            checksum=source_stat.checksum,
            size_bytes=source_stat.size_bytes,
            content_type=source_stat.content_type,
            state=ArtifactLifecycleState.READY.value,
        )
        session.add(artifact)

        job = Job(
            project_id=project.id,
            job_type=INGEST_JOB_TYPE,
            idempotency_key=ingest_idempotency_key(version.id),
            status="queued",
            attempt_count=0,
            max_attempts=max_attempts,
        )
        session.add(job)
        session.commit()
        return job.id, version.id, source_stat


def _service(store: LocalArtifactStore) -> IngestJobService:
    repository = SqlAlchemyIngestJobRepository(session_factory=SessionFactory)
    writer = SqlAlchemySourceLayerBatchWriter(session_factory=SessionFactory)
    pipeline = DatasetIngestPipeline(store=store, vector_writer=writer)
    return IngestJobService(repository=repository, pipeline=pipeline)


def test_real_geojson_ingest_is_ready_and_retry_is_noop(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "storage")
    job_id, version_id, source_stat = _create_ingest_state(
        store,
        source_bytes=_roads_geojson(),
    )
    service = _service(store)

    first = service.run(job_id=job_id, dataset_version_id=version_id)

    assert first.status is IngestJobRunStatus.SUCCEEDED
    assert first.attempt_count == 1
    assert first.details is not None
    assert first.details["inserted_rows"] == 2

    with SessionFactory() as session:
        version = session.get(DatasetVersion, version_id)
        job = session.get(Job, job_id)
        road_count = session.scalar(
            select(func.count())
            .select_from(SourceRoad)
            .where(SourceRoad.dataset_version_id == version_id)
        )
        geometry_rows = session.execute(
            text(
                "SELECT ST_SRID(geometry), ST_GeometryType(geometry) "
                "FROM source_roads WHERE dataset_version_id = :version_id ORDER BY name"
            ),
            {"version_id": version_id},
        ).all()
        artifact = session.scalar(
            select(Artifact).where(Artifact.uri == f"artifact://{source_stat.ref.key}")
        )

        assert version is not None and version.status == "ready"
        assert job is not None and job.status == "succeeded"
        assert job.attempt_count == 1
        assert job.started_at is not None
        assert job.finished_at is not None
        assert road_count == 2
        assert geometry_rows == [
            (WORKING_SRID, "ST_MultiLineString"),
            (WORKING_SRID, "ST_MultiLineString"),
        ]
        assert artifact is not None
        assert artifact.state == ArtifactLifecycleState.REFERENCED.value
        assert artifact.owner_type == "dataset_version"
        assert artifact.owner_id == version_id

    second = service.run(job_id=job_id, dataset_version_id=version_id)

    assert second.status is IngestJobRunStatus.ALREADY_READY
    assert second.attempt_count == 1
    with SessionFactory() as session:
        job = session.get(Job, job_id)
        count_after_retry = session.scalar(
            select(func.count())
            .select_from(SourceRoad)
            .where(SourceRoad.dataset_version_id == version_id)
        )
        assert job is not None and job.attempt_count == 1
        assert count_after_retry == 2


class _FlakyPipeline:
    def __init__(self, source_stat: ArtifactStat) -> None:
        self._source_stat = source_stat
        self.calls = 0

    def execute(self, context) -> IngestPipelineResult:
        self.calls += 1
        if self.calls == 1:
            raise TransientError("temporary storage failure")
        return IngestPipelineResult(
            source_artifact=self._source_stat,
            details={"kind": "test"},
        )


def test_transient_failure_retries_same_dataset_version(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "storage")
    job_id, version_id, source_stat = _create_ingest_state(
        store,
        source_bytes=_roads_geojson(),
    )
    repository = SqlAlchemyIngestJobRepository(session_factory=SessionFactory)
    pipeline = _FlakyPipeline(source_stat)
    service = IngestJobService(repository=repository, pipeline=pipeline)

    first = service.run(job_id=job_id, dataset_version_id=version_id)

    assert first.status is IngestJobRunStatus.FAILED
    assert first.retryable is True
    with SessionFactory() as session:
        version = session.get(DatasetVersion, version_id)
        job = session.get(Job, job_id)
        assert version is not None and version.status == "failed"
        assert job is not None and job.status == "failed"
        assert job.attempt_count == 1
        assert job.error_class == "transient"

    second = service.run(job_id=job_id, dataset_version_id=version_id)

    assert second.status is IngestJobRunStatus.SUCCEEDED
    assert second.attempt_count == 2
    with SessionFactory() as session:
        version = session.get(DatasetVersion, version_id)
        job = session.get(Job, job_id)
        assert version is not None and version.status == "ready"
        assert job is not None and job.status == "succeeded"
        assert job.attempt_count == 2
        assert job.error_class is None
        assert job.error_code is None
