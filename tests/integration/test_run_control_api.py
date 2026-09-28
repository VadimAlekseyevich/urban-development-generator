"""API acceptance for S12 authoritative run creation/control and progress reads."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult

WORKING_SRID = 32637
COMMIT = "a" * 40
client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _fixture(*, boundary: bool = True, ready: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Run control project",
                working_srid=WORKING_SRID,
                boundary=WKTElement(
                    "MULTIPOLYGON(((0 0, 100 0, 100 100, 0 100, 0 0)))",
                    srid=WORKING_SRID,
                ) if boundary else None,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            dataset = Dataset(project_id=project.id, kind="roads")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="ready" if ready else "uploaded",
                checksum_sha256="f" * 64,
                source_metadata={},
            )
            session.add(version)
            session.flush()
            return project.id, version.id


def _payload(version_id: uuid.UUID) -> dict[str, object]:
    return {
        "mode": "EXPANSION",
        "seed": 42,
        "dataset_version_ids": [str(version_id)],
        "config_json": {"scenario": {"density": "medium"}},
        "config_schema_version": "1",
        "commit_sha": COMMIT,
    }


def _create(project_id: uuid.UUID, version_id: uuid.UUID):
    return client.post(f"/api/v1/projects/{project_id}/runs", json=_payload(version_id))


def _counts() -> tuple[int, int, int]:
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(GenerationRun)) or 0,
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count()).select_from(JobOutbox)) or 0,
        )


def test_create_cancel_queued_and_idempotent_retry_are_atomic() -> None:
    project_id, version_id = _fixture()
    created = _create(project_id, version_id)
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["status"] == run["job"]["status"] == "queued"
    assert run["job"]["attempt_count"] == 0
    assert run["job"]["max_attempts"] == 3
    assert run["stages"] == []
    assert _counts() == (1, 1, 1)

    run_id = uuid.UUID(run["id"])
    with Session(engine) as session:
        stored = session.get(GenerationRun, run_id)
        assert stored is not None
        assert stored.config_json == {"scenario": {"density": "medium"}}
        assert [v.id for v in stored.dataset_versions] == [version_id]
        job = session.scalar(select(Job).where(Job.run_id == run_id))
        assert job is not None
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job.id))
        assert outbox is not None
        assert outbox.status == "pending"
        assert outbox.queue_name == "generation"
        assert outbox.payload == {"task": "run_generation", "run_id": str(run_id)}

    response = client.get(f"/api/v1/projects/{project_id}/runs")
    assert response.status_code == 200
    assert [r["id"] for r in response.json()["runs"]] == [str(run_id)]
    assert response.json()["truncated"] is False
    assert client.get(f"/api/v1/projects/{project_id}/runs/{run_id}").status_code == 200

    cancelled = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["job"]["status"] == "cancelled"
    assert cancelled.json()["job"]["cancel_requested_at"] is not None
    repeated = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/cancel")
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"
    assert _counts() == (1, 1, 1)

    retried = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/retry")
    assert retried.status_code == 201, retried.text
    retry_id = retried.json()["id"]
    assert retry_id != str(run_id)
    assert retried.json()["rerun_source_id"] == str(run_id)
    assert retried.json()["status"] == "queued"
    assert retried.json()["job"]["attempt_count"] == 0
    assert _counts() == (2, 2, 2)
    again = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/retry")
    assert again.status_code == 201
    assert again.json()["id"] == retry_id
    assert _counts() == (2, 2, 2)
    with Session(engine) as session:
        old = session.get(GenerationRun, run_id)
        new = session.get(GenerationRun, uuid.UUID(retry_id))
        assert old is not None and old.status == "cancelled"
        assert new is not None and new.config_json == old.config_json
        assert new.seed == old.seed and new.commit_sha == old.commit_sha
        assert [v.id for v in new.dataset_versions] == [version_id]


def test_running_cancel_sets_one_way_signal_and_reads_persisted_stage_progress() -> None:
    project_id, version_id = _fixture()
    created = _create(project_id, version_id)
    run_id = uuid.UUID(created.json()["id"])
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            run = session.get(GenerationRun, run_id)
            job = session.scalar(select(Job).where(Job.run_id == run_id))
            assert run is not None and job is not None
            run.status = "running"
            job.status = "running"
            job.attempt_count = 1
            session.add(
                RunStageResult(
                    run_id=run_id,
                    stage_name="prepare_snapshot",
                    stage_version="1",
                    status="running",
                    progress_percent=35,
                    input_hash="sha256:" + "1" * 64,
                    config_hash="sha256:" + "2" * 64,
                )
            )
    url = f"/api/v1/projects/{project_id}/runs/{run_id}"
    before = client.get(url)
    assert before.status_code == 200
    assert before.json()["stages"][0]["stage_name"] == "prepare_snapshot"
    assert before.json()["stages"][0]["progress_percent"] == 35
    requested = client.post(url + "/cancel")
    assert requested.status_code == 200
    assert requested.json()["status"] == "running"
    assert requested.json()["job"]["cancel_requested_at"] is not None
    assert client.post(url + "/cancel").json()["job"]["cancel_requested_at"] == (
        requested.json()["job"]["cancel_requested_at"]
    )
    with Session(engine) as session:
        run = session.get(GenerationRun, run_id)
        assert run is not None and run.status == "running"
        assert session.scalar(
            select(JobOutbox).join(Job, Job.id == JobOutbox.job_id).where(Job.run_id == run_id)
        ) is not None


def test_invalid_create_and_project_scoping_do_not_create_partial_jobs() -> None:
    project_id, version_id = _fixture()
    foreign_project, foreign_version = _fixture()
    no_boundary, no_boundary_version = _fixture(boundary=False)
    unready_project, unready_version = _fixture(ready=False)
    before = _counts()
    for target, payload, expected in (
        (project_id, {**_payload(version_id), "dataset_version_ids": [str(foreign_version)]}, 409),
        (project_id, {**_payload(version_id), "dataset_version_ids": [str(version_id)] * 2}, 422),
        (project_id, {**_payload(version_id), "commit_sha": "not-a-sha"}, 422),
        (project_id, {**_payload(version_id), "config_json": []}, 422),
        (no_boundary, _payload(no_boundary_version), 409),
        (unready_project, _payload(unready_version), 409),
        (uuid.uuid4(), _payload(version_id), 404),
    ):
        response = client.post(f"/api/v1/projects/{target}/runs", json=payload)
        assert response.status_code == expected, response.text
    assert _counts() == before

    created = _create(project_id, version_id)
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert client.get(f"/api/v1/projects/{foreign_project}/runs/{run_id}").status_code == 404
    assert client.post(
        f"/api/v1/projects/{foreign_project}/runs/{run_id}/cancel"
    ).status_code == 404
    assert client.post(
        f"/api/v1/projects/{foreign_project}/runs/{run_id}/retry"
    ).status_code == 404
    assert client.post(
        f"/api/v1/projects/{project_id}/runs/{run_id}/retry"
    ).status_code == 409
    assert client.get(f"/api/v1/projects/{uuid.uuid4()}/runs").status_code == 404
    assert client.get(f"/api/v1/projects/{project_id}/runs?limit=101").status_code == 422
    assert _counts() == (before[0] + 1, before[1] + 1, before[2] + 1)


def test_failed_source_retry_revalidates_ready_versions_and_preserves_source() -> None:
    project_id, version_id = _fixture()
    run_id = uuid.UUID(_create(project_id, version_id).json()["id"])
    with Session(engine) as session:
        with session.begin():
            run = session.get(GenerationRun, run_id)
            job = session.scalar(select(Job).where(Job.run_id == run_id))
            assert run is not None and job is not None
            run.status = "failed"
            job.status = "failed"
            session.get(DatasetVersion, version_id).status = "uploaded"  # type: ignore[union-attr]
    before = _counts()
    response = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/retry")
    assert response.status_code == 409, response.text
    assert _counts() == before
    with Session(engine) as session:
        source = session.get(GenerationRun, run_id)
        assert source is not None and source.status == "failed"
