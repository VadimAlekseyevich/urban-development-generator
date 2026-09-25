import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.run_metrics_writer import SqlAlchemyRunMetricsWriter
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


def _migrate_to_head() -> None:
    command.upgrade(Config("alembic.ini"), "head")


def _truncate_state() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    _truncate_state()
    yield
    _truncate_state()


def _create_project_and_run() -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Metrics dashboard integration",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            run = GenerationRun(
                project_id=project.id,
                status="running",
                mode="EXPANSION",
                seed=60,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="1",
            )
            session.add(run)
            session.flush()
            return project.id, run.id


def _score_result() -> CompositeScoreResult:
    return CompositeScoreResult(
        score=0.75,
        score_config_id="dashboard.default",
        score_config_version="1",
        normalization_profile_id="dashboard.profile",
        normalization_profile_version="1",
        metrics=(
            CompositeScoreMetricResult(
                metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
                raw_value=125000.0,
                normalized_value=0.8,
                normalization_policy_version="1",
                configured_weight=2.0,
                normalized_weight=0.5,
                contribution=0.4,
                was_clamped=False,
                was_missing=False,
            ),
            CompositeScoreMetricResult(
                metric_id=RawMetricId.ROADS_CONNECTED_COMPONENTS,
                raw_value=2.0,
                normalized_value=0.7,
                normalization_policy_version="1",
                configured_weight=2.0,
                normalized_weight=0.5,
                contribution=0.35,
                was_clamped=False,
                was_missing=False,
            ),
        ),
    )


def test_persisted_evaluation_is_delivered_without_recomputation() -> None:
    project_id, run_id = _create_project_and_run()
    write = SqlAlchemyRunMetricsWriter(
        session_factory=lambda: Session(engine)
    ).replace(
        run_id=run_id,
        result=_score_result(),
    )
    assert write.composite_score == pytest.approx(0.75)

    client = TestClient(app)
    runs_response = client.get(f"/api/v1/projects/{project_id}/metric-runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert runs["truncated"] is False
    assert len(runs["runs"]) == 1
    assert runs["runs"][0]["id"] == str(run_id)
    assert runs["runs"][0]["metric_count"] == 2
    assert runs["runs"][0]["composite_score"] == pytest.approx(0.75)

    dashboard_response = client.get(
        f"/api/v1/projects/{project_id}/metric-runs/{run_id}/metrics"
    )
    assert dashboard_response.status_code == 200
    dashboard = dashboard_response.json()
    assert dashboard["composite_score"] == pytest.approx(0.75)
    assert dashboard["score_config_id"] == "dashboard.default"
    assert dashboard["normalization_profile_id"] == "dashboard.profile"
    land = dashboard["metrics"][0]
    assert land["metric_id"] == "land.developed_area_m2"
    assert land["unit"] == "m2"
    assert land["raw_value"] == pytest.approx(125000.0)
    assert land["normalized_value"] == pytest.approx(0.8)
    assert land["normalized_weight"] == pytest.approx(0.5)
    assert land["contribution"] == pytest.approx(0.4)


def test_metric_dashboard_enforces_project_scope_and_availability() -> None:
    project_id, run_id = _create_project_and_run()
    other_project_id, _other_run_id = _create_project_and_run()
    client = TestClient(app)

    unavailable = client.get(
        f"/api/v1/projects/{project_id}/metric-runs/{run_id}/metrics"
    )
    assert unavailable.status_code == 409

    mismatch = client.get(
        f"/api/v1/projects/{other_project_id}/metric-runs/{run_id}/metrics"
    )
    assert mismatch.status_code == 404
