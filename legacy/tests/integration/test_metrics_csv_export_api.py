"""PostgreSQL/API acceptance for canonical raw-metric CSV export."""

from __future__ import annotations

import csv
import io
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.run_metrics_writer import composite_score_payload
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.score import (
    CompositeScoreMetricResult,
    CompositeScoreResult,
)

WORKING_SRID = 32637


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


def _create_project() -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="S13 metrics CSV fixture",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            return project.id


def _score(*, land: float, roads: float | None) -> dict[str, object]:
    result = CompositeScoreResult(
        score=0.5 if roads is not None else 0.25,
        score_config_id="csv.default",
        score_config_version="1",
        normalization_profile_id="csv.profile",
        normalization_profile_version="1",
        metrics=(
            CompositeScoreMetricResult(
                metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
                raw_value=land,
                normalized_value=0.5,
                normalization_policy_version="1",
                configured_weight=0.5,
                normalized_weight=0.5,
                contribution=0.25,
                was_clamped=False,
                was_missing=False,
            ),
            CompositeScoreMetricResult(
                metric_id=RawMetricId.ROADS_CONNECTED_COMPONENTS,
                raw_value=roads,
                normalized_value=0.5 if roads is not None else None,
                normalization_policy_version="1",
                configured_weight=0.5,
                normalized_weight=0.5,
                contribution=0.25 if roads is not None else 0.0,
                was_clamped=False,
                was_missing=roads is None,
            ),
        ),
    )
    return {"evaluation": composite_score_payload(result)}


def _run(
    project_id: uuid.UUID,
    *,
    seed: int,
    metrics_json: dict[str, object] | None,
    status: str = "succeeded",
) -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            run = GenerationRun(
                project_id=project_id,
                status="running",
                mode="EXPANSION",
                seed=seed,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="1",
                commit_sha="a" * 40,
                metrics_json=metrics_json,
            )
            session.add(run)
            session.flush()
            run.status = status
            return run.id


def _export(project_id: uuid.UUID, *run_ids: uuid.UUID):
    return TestClient(app).post(
        f"/api/v1/projects/{project_id}/exports/metrics.csv",
        json={"run_ids": [str(run_id) for run_id in run_ids]},
    )


def test_single_run_csv_exports_persisted_raw_metrics_without_validation() -> None:
    project_id = _create_project()
    run_id = _run(project_id, seed=7, metrics_json=_score(land=100.0, roads=2.0))

    response = _export(project_id, run_id)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["x-metrics-csv-schema"] == "raw-metrics-csv-v1"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="raw-metrics-{run_id}.csv"'
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [(row["metric_id"], row["raw_value"]) for row in rows] == [
        ("land.developed_area_m2", "100.0"),
        ("roads.connected_components", "2.0"),
    ]
    assert all(row["run_id"] == str(run_id) for row in rows)
    assert all(row["project_id"] == str(project_id) for row in rows)


def test_compare_csv_preserves_request_order_and_missing_raw_value() -> None:
    project_id = _create_project()
    first = _run(project_id, seed=1, metrics_json=_score(land=100.0, roads=2.0))
    second = _run(project_id, seed=2, metrics_json=_score(land=125.0, roads=None))

    response = _export(project_id, second, first)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        'attachment; filename="raw-metrics-compare-2-runs.csv"'
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))
    land_rows = [row for row in rows if row["metric_id"] == "land.developed_area_m2"]
    roads_rows = [row for row in rows if row["metric_id"] == "roads.connected_components"]
    assert [row["run_id"] for row in land_rows] == [str(second), str(first)]
    assert [row["run_order"] for row in land_rows] == ["1", "2"]
    assert [row["raw_value"] for row in land_rows] == ["125.0", "100.0"]
    assert [row["raw_value"] for row in roads_rows] == ["", "2.0"]
    assert [row["metric_present"] for row in roads_rows] == ["true", "true"]


def test_csv_export_enforces_project_success_and_persisted_metric_contract() -> None:
    project_id = _create_project()
    other_project = _create_project()
    good = _run(project_id, seed=1, metrics_json=_score(land=1.0, roads=1.0))
    other = _run(other_project, seed=2, metrics_json=_score(land=1.0, roads=1.0))
    pending = _run(
        project_id,
        seed=3,
        metrics_json=_score(land=1.0, roads=1.0),
        status="running",
    )
    unavailable = _run(project_id, seed=4, metrics_json=None)
    malformed = _run(
        project_id,
        seed=5,
        metrics_json={"evaluation": {"schema_version": "unsupported"}},
    )

    assert _export(project_id, good, good).status_code == 422
    assert _export(project_id, other).status_code == 404
    assert _export(uuid.uuid4(), good).status_code == 404
    assert _export(project_id, pending).status_code == 409
    assert _export(project_id, unavailable).status_code == 409
    assert _export(project_id, malformed).status_code == 500
    assert _export(project_id, *[uuid.uuid4() for _ in range(11)]).status_code == 422
