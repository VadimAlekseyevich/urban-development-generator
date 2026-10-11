"""Real DB and HTTP coverage of atomic project job capacity and backpressure."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.db.job_admission import (
    JobAdmissionLimits,
    JobCapacityExceeded,
    enforce_project_job_capacity,
)
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.artifact import Artifact
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project

client = TestClient(app)
COMMIT = "a" * 40


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None, monkeypatch: pytest.MonkeyPatch) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    monkeypatch.setattr(
        JobAdmissionLimits,
        "from_settings",
        classmethod(
            lambda cls: JobAdmissionLimits(
                project=2, generation=1, ingest=1, export=1
            )
        ),
    )
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _project() -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Job admission acceptance",
                working_srid=32637,
                boundary=WKTElement(
                    "MULTIPOLYGON(((0 0,100 0,100 100,0 100,0 0)))",
                    srid=32637,
                ),
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            dataset = Dataset(project_id=project.id, kind="roads")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id, version=1, status="ready",
                checksum_sha256="f" * 64, source_metadata={},
            )
            session.add(version)
            session.flush()
            return project.id, version.id


def _create(project_id: uuid.UUID, version_id: uuid.UUID):
    return client.post(
        f"/api/v1/projects/{project_id}/runs",
        json={
            "mode": "EXPANSION",
            "seed": 7,
            "dataset_version_ids": [str(version_id)],
            "config_json": {"scenario": {"density": "medium"}},
            "config_schema_version": "1",
            "commit_sha": COMMIT,
        },
    )


def _counts() -> tuple[int, int, int]:
    from backend.app.models.generation_run import GenerationRun

    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(GenerationRun)) or 0,
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count()).select_from(JobOutbox)) or 0,
        )


def test_active_generation_limit_returns_429_without_partial_job() -> None:
    project, version = _project()
    accepted = _create(project, version)
    assert accepted.status_code == 201, accepted.text
    assert _counts() == (1, 1, 1)

    saturated = _create(project, version)
    assert saturated.status_code == 429, saturated.text
    assert saturated.headers["retry-after"] == "30"
    assert saturated.json()["scope"] == "generation"
    assert saturated.json()["limit"] == 1
    assert _counts() == (1, 1, 1)

    cancelled = client.post(
        f"/api/v1/projects/{project}/runs/{accepted.json()['id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert _create(project, version).status_code == 201
    assert _counts() == (2, 2, 2)


def test_capacity_does_not_leak_across_project_boundaries() -> None:
    left, left_version = _project()
    right, right_version = _project()
    assert _create(left, left_version).status_code == 201
    assert _create(right, right_version).status_code == 201
    assert _create(left, left_version).status_code == 429
    assert _counts() == (2, 2, 2)


def test_aggregate_and_category_capacity_count_both_export_formats() -> None:
    project, version = _project()
    with Session(engine) as session:
        with session.begin():
            session.add_all((
                Job(project_id=project, job_type="geojson_export",
                    idempotency_key=f"one-{uuid.uuid4()}", status="queued"),
                Job(project_id=project, job_type="geopackage_export",
                    idempotency_key=f"two-{uuid.uuid4()}", status="running"),
            ))
    with Session(engine) as session:
        with pytest.raises(JobCapacityExceeded, match="project"):
            enforce_project_job_capacity(
                session, project_id=project, job_type="generation_run"
            )

    with Session(engine) as session:
        with session.begin():
            for job in session.scalars(select(Job).where(Job.project_id == project)):
                job.status = "succeeded"
    assert _create(project, version).status_code == 201


def test_ingest_limit_preserves_idempotent_replay_and_rejects_new_artifact() -> None:
    project, _ = _project()
    with Session(engine) as session:
        with session.begin():
            artifact_ids: list[uuid.UUID] = []
            for _ in range(2):
                artifact_id = uuid.uuid4()
                artifact_ids.append(artifact_id)
                session.add(
                    Artifact(
                        id=artifact_id,
                        uri=f"artifact://uploads/{artifact_id}/roads.geojson",
                        checksum="sha256:" + "d" * 64,
                        size_bytes=123,
                        content_type="application/geo+json",
                        state="ready",
                    )
                )
    def post(artifact_id: uuid.UUID):
        return client.post(
            f"/api/v1/projects/{project}/ingests",
            json={
                "artifact_id": str(artifact_id),
                "dataset_kind": "roads",
                "source_format": "geojson",
            },
        )

    first = post(artifact_ids[0])
    assert first.status_code == 201, first.text
    assert post(artifact_ids[0]).status_code == 201
    assert _counts()[1:] == (1, 1)

    second = post(artifact_ids[1])
    assert second.status_code == 429, second.text
    assert second.json()["scope"] == "ingest"
    assert second.headers["retry-after"] == "30"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetVersion)) == 2
    # One pre-existing fixture version plus the first accepted ingest.
    assert _counts()[1:] == (1, 1)


def test_invalid_admission_bounds_cannot_be_silently_disabled() -> None:
    with pytest.raises(ValueError):
        JobAdmissionLimits(project=0, generation=1, ingest=1, export=1)
    with pytest.raises(ValueError):
        JobAdmissionLimits(project=129, generation=1, ingest=1, export=1)
