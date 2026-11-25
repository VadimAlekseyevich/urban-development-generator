import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform as shapely_transform
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.run_validation_writer import (
    RunValidationImmutableError,
    SqlAlchemyRunValidationWriter,
)
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.domain import (
    ConstraintEntityRef,
    ConstraintProblemGeometry,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    SoftPenaltyMetadata,
    ValidationReport,
)

WORKING_SRID = 3857
_TO_WORKING = Transformer.from_crs(4326, WORKING_SRID, always_xy=True)


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
                name="Validation layer integration",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            run = GenerationRun(
                project_id=project.id,
                status="running",
                mode="EXPANSION",
                seed=59,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="1",
            )
            session.add(run)
            session.flush()
            return project.id, run.id


def _report(*, message: str = "Road crosses protected area") -> ValidationReport:
    geometry = shapely_transform(
        _TO_WORKING.transform,
        box(-0.13, 51.50, -0.11, 51.52),
    )
    return ValidationReport(
        results=(
            ConstraintResult(
                code="roads.forbidden_crossing",
                severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.ROAD,
                passed=False,
                message=message,
                entity_ref=ConstraintEntityRef(
                    entity_id="road:generated:integration"
                ),
                problem_geometry=ConstraintProblemGeometry(
                    geometry=geometry,
                    working_srid=WORKING_SRID,
                ),
            ),
            ConstraintResult(
                code="buildings.orientation",
                severity=ConstraintSeverity.SOFT,
                scope=ConstraintScope.BUILDING,
                passed=False,
                message="Preferred orientation is not satisfied",
                entity_ref=ConstraintEntityRef(
                    entity_id="building:generated:integration"
                ),
                soft_penalty=SoftPenaltyMetadata(
                    raw_penalty=0.2,
                    weight=1.5,
                ),
            ),
            ConstraintResult(
                code="territory.coverage",
                severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.TERRITORY,
                passed=True,
                message="Coverage is valid",
            ),
        )
    )


def _writer() -> SqlAlchemyRunValidationWriter:
    return SqlAlchemyRunValidationWriter(
        session_factory=lambda: Session(engine)
    )


def test_validation_writer_and_api_preserve_canonical_detail() -> None:
    project_id, run_id = _create_project_and_run()
    write = _writer().replace(run_id=run_id, report=_report())

    assert write.result_count == 3
    assert write.violation_count == 2
    assert write.spatial_violation_count == 1

    client = TestClient(app)
    runs_response = client.get(
        f"/api/v1/projects/{project_id}/validation-runs"
    )
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert runs["truncated"] is False
    assert len(runs["runs"]) == 1
    summary = runs["runs"][0]
    assert summary["id"] == str(run_id)
    assert summary["violation_count"] == 2
    assert summary["hard_violation_count"] == 1
    assert summary["soft_violation_count"] == 1
    assert summary["spatial_violation_count"] == 1

    details_response = client.get(
        f"/api/v1/projects/{project_id}"
        f"/validation-runs/{run_id}/violations"
    )
    assert details_response.status_code == 200
    details = details_response.json()
    assert details["total"] == 2
    assert details["violations"][0]["violation_index"] == 0
    assert details["violations"][0]["entity_id"] == (
        "road:generated:integration"
    )
    assert details["violations"][1]["soft_penalty"][
        "weighted_penalty"
    ] == pytest.approx(0.3)

    geojson_response = client.get(
        f"/api/v1/projects/{project_id}"
        f"/validation-runs/{run_id}/violations/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert geojson_response.status_code == 200
    geojson = geojson_response.json()
    assert geojson["type"] == "FeatureCollection"
    assert geojson["geojson_crs"] == "EPSG:4326"
    assert geojson["matching_count"] == 1
    assert geojson["features"][0]["properties"]["severity"] == "HARD"
    assert geojson["features"][0]["properties"]["message"] == (
        "Road crosses protected area"
    )


def test_validation_writer_is_retry_safe_and_success_is_immutable() -> None:
    _project_id, run_id = _create_project_and_run()
    writer = _writer()
    writer.replace(run_id=run_id, report=_report(message="first"))
    writer.replace(run_id=run_id, report=_report(message="replacement"))

    with Session(engine) as session:
        with session.begin():
            run = session.get(GenerationRun, run_id)
            assert run is not None
            assert run.validation_json is not None
            assert run.validation_json["results"][0]["message"] == "replacement"
            run.commit_sha = "a" * 40
            run.status = "succeeded"

    with pytest.raises(RunValidationImmutableError):
        writer.replace(run_id=run_id, report=_report(message="late"))

    response = TestClient(app).get(
        f"/api/v1/projects/{_project_id}"
        f"/validation-runs/{run_id}/violations"
    )
    assert response.status_code == 200
    assert response.json()["violations"][0]["message"] == "replacement"


def test_validation_api_enforces_project_scope_and_bbox_contract() -> None:
    project_id, run_id = _create_project_and_run()
    _writer().replace(run_id=run_id, report=_report())
    other_project_id, _other_run_id = _create_project_and_run()

    mismatch = TestClient(app).get(
        f"/api/v1/projects/{other_project_id}"
        f"/validation-runs/{run_id}/violations"
    )
    assert mismatch.status_code == 404

    bad_bbox = TestClient(app).get(
        f"/api/v1/projects/{project_id}"
        f"/validation-runs/{run_id}/violations/geojson",
        params={"bbox": "bad"},
    )
    assert bad_bbox.status_code == 422
