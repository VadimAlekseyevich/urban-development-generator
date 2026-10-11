"""No mocked persistence: real upload -> version/job/outbox -> ingest -> PostGIS."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.adapters import LocalArtifactStore
from backend.app.api.dependencies import get_upload_service
from backend.app.application.ingest import IngestJobRunStatus, IngestJobService
from backend.app.application.uploads import UploadService
from backend.app.db.artifact_repository import SqlAlchemyArtifactRepository
from backend.app.db.ingest_job_repository import SqlAlchemyIngestJobRepository
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.artifact import Artifact
from backend.app.models.dataset import DatasetVersion
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.source_layer import SourceRoad
from backend.app.services.ingest_pipeline import DatasetIngestPipeline


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as con:
        con.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    app.dependency_overrides.pop(get_upload_service, None)
    with engine.begin() as con:
        con.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _project(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "working_srid": 3857},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_real_artifact_to_ready_dataset_version_and_outbox(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "storage")
    def upload_provider():  # type: ignore[no-untyped-def]
        with Session(engine) as session:
            yield UploadService(
                store, SqlAlchemyArtifactRepository(session),
                max_size_bytes=1024 * 1024,
            )

    app.dependency_overrides[get_upload_service] = upload_provider
    client = TestClient(app)
    project_id = _project(client, "Real ingest project")
    other_id = _project(client, "Different project")

    raw = (Path("tests/fixtures/ingest") / "roads.geojson").read_bytes()
    uploaded = client.post(
        "/api/v1/uploads",
        files={"file": ("roads.geojson", raw, "application/geo+json")},
    )
    assert uploaded.status_code == 201, uploaded.text
    artifact_id = uploaded.json()["artifact_id"]
    assert uploaded.json()["checksum"].startswith("sha256:")

    payload = {
        "artifact_id": artifact_id,
        "dataset_kind": "roads",
        "source_format": "geojson",
    }
    started = client.post(f"/api/v1/projects/{project_id}/ingests", json=payload)
    assert started.status_code == 201, started.text
    state = started.json()
    assert state["project_id"] == project_id
    assert state["status"] == state["job_status"] == "queued" or (
        state["status"] == "uploaded" and state["job_status"] == "queued"
    )
    version_id, job_id = state["dataset_version_id"], state["job_id"]

    duplicate = client.post(f"/api/v1/projects/{project_id}/ingests", json=payload)
    assert duplicate.status_code == 201
    assert duplicate.json()["dataset_version_id"] == version_id
    assert duplicate.json()["job_id"] == job_id
    foreign = client.post(f"/api/v1/projects/{other_id}/ingests", json=payload)
    assert foreign.status_code == 409
    invalid = client.post(
        f"/api/v1/projects/{project_id}/ingests",
        json={**payload, "dataset_kind": "dem"},
    )
    assert invalid.status_code == 422
    unknown = client.post(
        f"/api/v1/projects/{project_id}/ingests",
        json={**payload, "artifact_id": str(uuid.uuid4())},
    )
    assert unknown.status_code == 404

    with Session(engine) as session:
        outbox = session.scalar(
            select(JobOutbox).where(JobOutbox.job_id == uuid.UUID(job_id))
        )
        assert outbox is not None
        assert outbox.queue_name == "ingest"
        assert outbox.status == "pending"
        assert outbox.payload == {
            "task": "run_ingest",
            "job_id": job_id,
            "dataset_version_id": version_id,
        }
        version = session.get(DatasetVersion, uuid.UUID(version_id))
        assert version is not None and version.status == "uploaded"
        assert version.checksum_sha256 == uploaded.json()["checksum"].removeprefix("sha256:")

    # This executes the exact canonical worker-side pipeline, not a synthetic
    # version insert or a test-specific ready-state mutation.
    worker = IngestJobService(
        repository=SqlAlchemyIngestJobRepository(),
        pipeline=DatasetIngestPipeline(store=store),
    )
    outcome = worker.run(job_id=uuid.UUID(job_id), dataset_version_id=uuid.UUID(version_id))
    assert outcome.status == IngestJobRunStatus.SUCCEEDED
    assert worker.run(
        job_id=uuid.UUID(job_id), dataset_version_id=uuid.UUID(version_id)
    ).status == IngestJobRunStatus.ALREADY_READY

    poll = client.get(f"/api/v1/projects/{project_id}/ingests/{version_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] == poll.json()["job_status"] == "ready" or (
        poll.json()["status"] == "ready" and poll.json()["job_status"] == "succeeded"
    )
    assert client.get(f"/api/v1/projects/{other_id}/ingests/{version_id}").status_code == 404

    with Session(engine) as session:
        rows = session.scalar(
            select(func.count())
            .select_from(SourceRoad)
            .where(SourceRoad.dataset_version_id == uuid.UUID(version_id))
        )
        assert rows is not None and rows > 0
        artifact = session.get(Artifact, uuid.UUID(artifact_id))
        assert artifact is not None
        assert artifact.state == "referenced"
        assert artifact.owner_id == uuid.UUID(version_id)
        job = session.get(Job, uuid.UUID(job_id))
        assert job is not None and job.status == "succeeded"
    listed = client.get(f"/api/v1/projects/{project_id}/dataset-versions")
    assert listed.status_code == 200
    assert listed.json()["versions"][0]["id"] == version_id
    assert listed.json()["versions"][0]["status"] == "ready"
