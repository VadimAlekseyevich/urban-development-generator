import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import transform as shapely_transform
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.generated_entity import GeneratedBuilding
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project

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


def _polygon(
    *,
    west: float,
    south: float,
    east: float,
    north: float,
) -> Polygon:
    source = Polygon(
        [
            (west, south),
            (east, south),
            (east, north),
            (west, north),
            (west, south),
        ]
    )
    return shapely_transform(_TO_WORKING.transform, source)


def _insert_building(
    *,
    run_id: uuid.UUID,
    building_key: str,
    west: float,
    south: float,
    east: float,
    north: float,
    archetype: str,
    building_use: str,
    floors: int,
) -> None:
    geometry = _polygon(
        west=west,
        south=south,
        east=east,
        north=north,
    )
    area = float(geometry.area)
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            session.add(
                GeneratedBuilding(
                    run_id=run_id,
                    building_key=building_key,
                    source_id=f"parcel:{building_key}",
                    zone_class="residential",
                    archetype=archetype,
                    building_use=building_use,
                    floors=floors,
                    footprint_area_m2=area,
                    gfa_m2=area * floors,
                    attributes_json={
                        "source_kind": "parcel",
                        "attribute_config_version": "attrs-v1",
                    },
                    geometry=from_shape(
                        geometry,
                        srid=WORKING_SRID,
                        extended=True,
                    ),
                )
            )


def test_building_runs_and_geojson_are_project_scoped() -> None:
    project_id = _create_project("Building viewport")
    run_id = _create_run(project_id=project_id, seed=91)
    empty_run_id = _create_run(project_id=project_id, seed=92)
    other_project_id = _create_project("Other building project")
    other_run_id = _create_run(project_id=other_project_id, seed=93)

    _insert_building(
        run_id=run_id,
        building_key="building:london",
        west=-0.125,
        south=51.505,
        east=-0.124,
        north=51.506,
        archetype="point",
        building_use="residential",
        floors=6,
    )
    _insert_building(
        run_id=run_id,
        building_key="building:paris",
        west=2.32,
        south=48.855,
        east=2.321,
        north=48.856,
        archetype="bar",
        building_use="mixed",
        floors=5,
    )
    _insert_building(
        run_id=other_run_id,
        building_key="building:other",
        west=-0.123,
        south=51.505,
        east=-0.122,
        north=51.506,
        archetype="commercial",
        building_use="commercial",
        floors=4,
    )

    client = TestClient(app)
    runs_response = client.get(f"/api/v1/projects/{project_id}/building-runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert {item["id"] for item in runs} == {str(run_id), str(empty_run_id)}
    counts = {
        item["id"]: item["generated_building_count"]
        for item in runs
    }
    assert counts[str(run_id)] == 2
    assert counts[str(empty_run_id)] == 0

    response = client.get(
        f"/api/v1/projects/{project_id}/building-runs/{run_id}/buildings/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert payload["geojson_crs"] == "EPSG:4326"
    assert payload["working_srid"] == WORKING_SRID
    assert payload["truncated"] is False
    assert len(payload["features"]) == 1

    feature = payload["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    properties = feature["properties"]
    assert properties["building_key"] == "building:london"
    assert properties["source_id"] == "parcel:building:london"
    assert properties["zone_class"] == "residential"
    assert properties["archetype"] == "point"
    assert properties["building_use"] == "residential"
    assert properties["floors"] == 6
    assert properties["footprint_area_m2"] > 0
    assert properties["gfa_m2"] > properties["footprint_area_m2"]
    assert properties["source_kind"] == "parcel"
    assert properties["attribute_config_version"] == "attrs-v1"

    mismatch = client.get(
        f"/api/v1/projects/{project_id}/building-runs/"
        f"{other_run_id}/buildings/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6"},
    )
    assert mismatch.status_code == 404

    missing_project = client.get(
        f"/api/v1/projects/{uuid.uuid4()}/building-runs"
    )
    assert missing_project.status_code == 404


def test_building_geojson_rejects_invalid_bbox() -> None:
    project_id = _create_project("Invalid building bbox")
    run_id = _create_run(project_id=project_id, seed=101)
    response = TestClient(app).get(
        f"/api/v1/projects/{project_id}/building-runs/"
        f"{run_id}/buildings/geojson",
        params={"bbox": "bad"},
    )
    assert response.status_code == 422
