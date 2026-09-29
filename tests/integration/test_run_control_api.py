"""API acceptance for S12 authoritative run creation/control and progress reads."""

from __future__ import annotations

import json
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.db.run_metrics_writer import composite_score_payload
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import ValidationReport, serialize_validation_report
from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.score import (
    CompositeScoreMetricResult,
    CompositeScoreResult,
)

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


def test_failed_source_retry_revalidates_current_refs_without_reopening_ready() -> None:
    project_id, version_id = _fixture()
    run_id = uuid.UUID(_create(project_id, version_id).json()["id"])
    with Session(engine) as session:
        with session.begin():
            run = session.get(GenerationRun, run_id)
            job = session.scalar(select(Job).where(Job.run_id == run_id))
            ready = session.get(DatasetVersion, version_id)
            assert run is not None and job is not None and ready is not None
            run.status = "failed"
            job.status = "failed"
            # Published source versions cannot be reverted to uploaded just to
            # simulate a failing retry. A *failed* run's dataset references
            # remain mutable, so point it at a new, unpublished version.
            unready = DatasetVersion(
                dataset_id=ready.dataset_id,
                version=2,
                status="uploaded",
                checksum_sha256="e" * 64,
                source_metadata={},
            )
            session.add(unready)
            session.flush()
            run.dataset_versions = [unready]
    before = _counts()
    response = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/retry")
    assert response.status_code == 409, response.text
    assert _counts() == before
    with Session(engine) as session:
        source = session.get(GenerationRun, run_id)
        assert source is not None and source.status == "failed"


def _persisted_score(raw_area: float, score: float) -> dict[str, object]:
    metric = CompositeScoreMetricResult(
        metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
        raw_value=raw_area,
        normalized_value=score,
        normalization_policy_version="1",
        configured_weight=1.0,
        normalized_weight=1.0,
        contribution=score,
        was_clamped=False,
        was_missing=False,
    )
    return {
        "evaluation": composite_score_payload(
            CompositeScoreResult(
                score=score,
                score_config_id="workflow-fixture",
                score_config_version="1",
                normalization_profile_id="workflow-profile",
                normalization_profile_version="1",
                metrics=(metric,),
            )
        )
    }


def test_m4_created_runs_compare_only_persisted_inputs_without_mutation() -> None:
    """Integration gate: create -> persisted success -> compare -> scoped read."""
    project_id, version_id = _fixture()
    foreign_project, _foreign_version = _fixture()
    first = _create(project_id, version_id)
    second = _create(project_id, version_id)
    assert first.status_code == second.status_code == 201
    ids = (uuid.UUID(first.json()["id"]), uuid.UUID(second.json()["id"]))

    # Simulate canonical worker publication after the API's atomic job/outbox
    # creation. This acceptance checks persistence/HTTP wiring, not GIS execution.
    with Session(engine) as session:
        with session.begin():
            for run_id, area, score in zip(ids, (10.0, 20.0), (0.5, 0.75), strict=True):
                run = session.get(GenerationRun, run_id)
                job = session.scalar(select(Job).where(Job.run_id == run_id))
                assert run is not None and job is not None
                run.status = job.status = "running"
                session.flush()
                run.metrics_json = _persisted_score(area, score)
                run.validation_json = json.loads(
                    serialize_validation_report(ValidationReport(results=()))
                )
                run.status = job.status = "succeeded"

    before = _counts()
    response = client.post(
        f"/api/v1/projects/{project_id}/compare",
        json={"run_ids": [str(ids[0]), str(ids[1])]},
    )
    assert response.status_code == 200, response.text
    compared = response.json()
    assert compared["run_ids"] == [str(run_id) for run_id in ids]
    assert compared["baseline_run_id"] == str(ids[0])
    assert compared["scores_comparable"] is True
    assert [run["score_rank"] for run in compared["runs"]] == [2, 1]
    area_metric = next(
        metric for metric in compared["metrics"]
        if metric["metric_id"] == RawMetricId.LAND_DEVELOPED_AREA_M2.value
    )
    assert area_metric["values"][1]["delta_from_baseline"] == 10.0
    assert all(run["validation"]["violation_count"] == 0 for run in compared["runs"])
    assert _counts() == before

    for run_id in ids:
        snapshot = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["status"] == snapshot.json()["job"]["status"] == "succeeded"
        assert client.post(
            f"/api/v1/projects/{project_id}/runs/{run_id}/cancel"
        ).json()["status"] == "succeeded"
        assert client.post(
            f"/api/v1/projects/{project_id}/runs/{run_id}/retry"
        ).status_code == 409
    assert client.post(
        f"/api/v1/projects/{foreign_project}/compare",
        json={"run_ids": [str(run_id) for run_id in ids]},
    ).status_code == 404
    assert _counts() == before
