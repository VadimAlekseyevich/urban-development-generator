"""PostgreSQL/API acceptance for bounded, read-only persisted run comparisons."""

from __future__ import annotations

import json
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from shapely.geometry import Point
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.run_metrics_writer import composite_score_payload
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.domain import (
    ConstraintProblemGeometry,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    ValidationReport,
    serialize_validation_report,
)
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


def _score(
    *,
    land: float = 50.0,
    roads: float = 2.0,
    score: float = 0.75,
    score_config: str = "default",
) -> dict[str, object]:
    result = CompositeScoreResult(
        score=score,
        score_config_id=score_config,
        score_config_version="1",
        normalization_profile_id="profile",
        normalization_profile_version="1",
        metrics=(
            CompositeScoreMetricResult(
                metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
                raw_value=land,
                normalized_value=score,
                normalization_policy_version="1",
                configured_weight=0.5,
                normalized_weight=0.5,
                contribution=score / 2,
                was_clamped=False,
                was_missing=False,
            ),
            CompositeScoreMetricResult(
                metric_id=RawMetricId.ROADS_CONNECTED_COMPONENTS,
                raw_value=roads,
                normalized_value=score,
                normalization_policy_version="1",
                configured_weight=0.5,
                normalized_weight=0.5,
                contribution=score / 2,
                was_clamped=False,
                was_missing=False,
            ),
        ),
    )
    return {"evaluation": composite_score_payload(result)}


def _report(*, with_failures: bool = True) -> dict[str, object]:
    results = ()
    if with_failures:
        results = (
            ConstraintResult(
                code="roads.connected",
                severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.ROAD,
                passed=False,
                message="Network is disconnected",
                problem_geometry=ConstraintProblemGeometry(
                    geometry=Point(1, 1),
                    working_srid=WORKING_SRID,
                ),
            ),
            ConstraintResult(
                code="roads.soft_penalty",
                severity=ConstraintSeverity.SOFT,
                scope=ConstraintScope.ROAD,
                passed=False,
                message="Soft penalty",
            ),
            ConstraintResult(
                code="roads.passed",
                severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.ROAD,
                passed=True,
                message="Passing check",
            ),
        )
    payload: dict[str, object] = json.loads(
        serialize_validation_report(ValidationReport(results=results))
    )
    return payload


def _create_project() -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="S12 compare fixture",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            return project.id


def _run(
    project_id: uuid.UUID,
    *,
    seed: int,
    score: dict[str, object] | None = None,
    validation: dict[str, object] | None = None,
    status: str = "succeeded",
    working_srid: int = WORKING_SRID,
) -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            run = GenerationRun(
                project_id=project_id,
                status="running",
                mode="EXPANSION",
                seed=seed,
                working_srid=working_srid,
                config_json={},
                config_schema_version="1",
                commit_sha="a" * 40,
                metrics_json=score,
                validation_json=validation,
            )
            session.add(run)
            session.flush()
            run.status = status
            return run.id


def _compare(project_id: uuid.UUID, *run_ids: uuid.UUID):
    return TestClient(app).post(
        f"/api/v1/projects/{project_id}/compare",
        json={"run_ids": [str(run_id) for run_id in run_ids]},
    )


def test_compare_reuses_persisted_metric_and_validation_with_stable_order() -> None:
    project_id = _create_project()
    first = _run(project_id, seed=7, score=_score(), validation=_report())
    second = _run(
        project_id, seed=8, score=_score(land=100, roads=1, score=0.85),
        validation=_report(with_failures=False),
    )
    response = _compare(project_id, first, second)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["run_ids"] == [str(first), str(second)]
    assert result["baseline_run_id"] == str(first)
    assert result["scores_comparable"] is True
    assert [item["composite_score"] for item in result["runs"]] == [0.75, 0.85]
    assert [item["score_rank"] for item in result["runs"]] == [2, 1]
    assert result["runs"][1]["score_delta_from_baseline"] == pytest.approx(0.10)
    assert result["runs"][0]["validation"] == {
        "violation_count": 2,
        "hard_violation_count": 1,
        "soft_violation_count": 1,
        "spatial_violation_count": 1,
    }
    assert result["runs"][1]["validation"]["violation_count"] == 0
    assert [metric["metric_id"] for metric in result["metrics"]] == [
        "land.developed_area_m2", "roads.connected_components"
    ]
    land, roads = result["metrics"]
    assert land["unit"] == "m2" and land["values"][1]["delta_from_baseline"] == 50.0
    assert land["values"][0]["rank"] is None
    assert land["values"][1]["rank"] is None  # TARGET has no universal rank
    assert roads["unit"] == "count"
    assert [item["rank"] for item in roads["values"]] == [2, 1]
    assert [item["delta_from_baseline"] for item in roads["values"]] == [0.0, -1.0]
    assert response.json() == _compare(project_id, first, second).json()
    with Session(engine) as session:
        assert session.get(GenerationRun, first).status == "succeeded"
        assert session.get(GenerationRun, second).status == "succeeded"


def test_compare_preserves_input_order_and_disables_incompatible_score_rank() -> None:
    project_id = _create_project()
    first = _run(project_id, seed=7, score=_score(), validation=_report())
    second = _run(
        project_id, seed=8, score=_score(score_config="different", score=0.5),
        validation=_report(with_failures=False),
    )
    response = _compare(project_id, second, first)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["baseline_run_id"] == str(second)
    assert result["scores_comparable"] is False
    assert all(item["score_rank"] is None for item in result["runs"])
    assert all(item["score_delta_from_baseline"] is None for item in result["runs"])
    assert result["metrics"][0]["values"][0]["run_id"] == str(second)


def test_compare_rejects_invalid_scope_cardinality_and_missing_snapshots() -> None:
    project_id = _create_project()
    other_project = _create_project()
    first = _run(project_id, seed=1, score=_score(), validation=_report())
    other = _run(other_project, seed=2, score=_score(), validation=_report())
    missing_validation = _run(project_id, seed=3, score=_score())
    missing_score = _run(project_id, seed=4, validation=_report())
    pending = _run(
        project_id, seed=5, score=_score(), validation=_report(), status="running"
    )
    wrong_srid = _run(
        project_id, seed=6, score=_score(), validation=_report(), working_srid=32638
    )

    assert _compare(project_id, first, first).status_code == 422
    assert _compare(project_id, first).status_code == 422
    assert _compare(project_id, first, other).status_code == 404
    assert _compare(uuid.uuid4(), first, other).status_code == 404
    assert _compare(project_id, first, uuid.uuid4()).status_code == 404
    assert _compare(project_id, first, missing_validation).status_code == 409
    assert _compare(project_id, first, missing_score).status_code == 409
    assert _compare(project_id, first, pending).status_code == 409
    assert _compare(project_id, first, wrong_srid).status_code == 409
    assert _compare(project_id, *[uuid.uuid4() for _ in range(11)]).status_code == 422


def test_compare_fails_closed_on_malformed_persisted_validation_or_evaluation() -> None:
    project_id = _create_project()
    first = _run(project_id, seed=1, score=_score(), validation=_report())
    bad_validation = _report()
    bad_validation["schema_version"] = 999
    second = _run(project_id, seed=2, score=_score(), validation=bad_validation)
    assert _compare(project_id, first, second).status_code == 500

    score = _score()
    assert isinstance(score["evaluation"], dict)
    score["evaluation"]["schema_version"] = "unsupported"
    third = _run(project_id, seed=3, score=score, validation=_report())
    assert _compare(project_id, first, third).status_code == 500
