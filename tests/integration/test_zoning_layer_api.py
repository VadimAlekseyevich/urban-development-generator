import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import transform as shapely_transform
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.generated_entity import GeneratedZone
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project

WORKING_SRID = 3857
_TO_WORKING = Transformer.from_crs(4326, WORKING_SRID, always_xy=True)


def _migrate_to_head() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")


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


def _create_project(name: str) -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name=name,
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            return project.id


def _create_run(*, project_id: uuid.UUID, seed: int) -> uuid.UUID:
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
            )
            session.add(run)
            session.flush()
            return run.id


def _zone_geometry(
    *,
    west: float,
    south: float,
    east: float,
    north: float,
) -> object:
    polygon = Polygon(
        [
            (west, south),
            (east, south),
            (east, north),
            (west, north),
            (west, south),
        ]
    )
    projected = shapely_transform(_TO_WORKING.transform, polygon)
    return from_shape(
        MultiPolygon([projected]),
        srid=WORKING_SRID,
        extended=True,
    )


def _insert_zone(
    *,
    run_id: uuid.UUID,
    zone_class: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            session.add(
                GeneratedZone(
                    run_id=run_id,
                    zone_class=zone_class,
                    area_m2=1000.0,
                    attributes_json={"fixture": zone_class},
                    diagnostics_json={"source": "test"},
                    geometry=_zone_geometry(
                        west=west,
                        south=south,
                        east=east,
                        north=north,
                    ),
                )
            )


def test_zoning_runs_and_generated_zone_geojson_are_project_scoped() -> None:
    project_id = _create_project("Zoning viewport")
    run_id = _create_run(project_id=project_id, seed=11)
    empty_run_id = _create_run(project_id=project_id, seed=12)
    other_project_id = _create_project("Other zoning project")
    other_run_id = _create_run(project_id=other_project_id, seed=13)

    _insert_zone(
        run_id=run_id,
        zone_class="residential",
        west=-0.13,
        south=51.49,
        east=-0.09,
        north=51.53,
    )
    _insert_zone(
        run_id=run_id,
        zone_class="recreation",
        west=2.30,
        south=48.84,
        east=2.34,
        north=48.88,
    )
    _insert_zone(
        run_id=other_run_id,
        zone_class="mixed",
        west=-0.12,
        south=51.50,
        east=-0.08,
        north=51.54,
    )

    client = TestClient(app)
    runs_response = client.get(f"/api/v1/projects/{project_id}/zoning-runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert {item["id"] for item in runs} == {str(run_id), str(empty_run_id)}
    counts = {item["id"]: item["zone_count"] for item in runs}
    assert counts[str(run_id)] == 2
    assert counts[str(empty_run_id)] == 0
    assert all(item["project_id"] == str(project_id) for item in runs)

    zones_response = client.get(
        f"/api/v1/projects/{project_id}/zoning-runs/{run_id}/zones/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert zones_response.status_code == 200
    body = zones_response.json()
    assert body["type"] == "FeatureCollection"
    assert body["project_id"] == str(project_id)
    assert body["run_id"] == str(run_id)
    assert body["geojson_crs"] == "EPSG:4326"
    assert body["working_srid"] == WORKING_SRID
    assert body["truncated"] is False
    assert len(body["features"]) == 1
    feature = body["features"][0]
    assert feature["geometry"]["type"] == "MultiPolygon"
    assert feature["properties"]["zone_class"] == "residential"
    assert feature["properties"]["area_m2"] == 1000.0
    assert feature["properties"]["attributes"]["fixture"] == "residential"
    assert feature["properties"]["diagnostics"]["source"] == "test"

    mismatch = client.get(
        f"/api/v1/projects/{project_id}/zoning-runs/{other_run_id}/zones/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6"},
    )
    assert mismatch.status_code == 404

    missing_project = client.get(
        f"/api/v1/projects/{uuid.uuid4()}/zoning-runs"
    )
    assert missing_project.status_code == 404


def test_generated_zone_geojson_rejects_invalid_bbox() -> None:
    project_id = _create_project("Invalid zoning bbox")
    run_id = _create_run(project_id=project_id, seed=21)
    response = TestClient(app).get(
        f"/api/v1/projects/{project_id}/zoning-runs/{run_id}/zones/geojson",
        params={"bbox": "bad"},
    )
    assert response.status_code == 422
