import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import transform as shapely_transform
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.generated_entity import GeneratedRoad
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


def _road_geometry(*, start: tuple[float, float], end: tuple[float, float]) -> object:
    line = LineString([start, end])
    projected = shapely_transform(_TO_WORKING.transform, line)
    return from_shape(projected, srid=WORKING_SRID, extended=True)


def _insert_road(
    *,
    run_id: uuid.UUID,
    edge_id: str,
    road_id: str,
    source_node_id: str,
    target_node_id: str,
    road_class: str,
    origin: str,
    length_m: float,
    start: tuple[float, float],
    end: tuple[float, float],
) -> None:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            session.add(
                GeneratedRoad(
                    run_id=run_id,
                    attributes_json={
                        "edge_id": edge_id,
                        "road_id": road_id,
                        "part_index": 0,
                        "length_m": length_m,
                        "road_class": road_class,
                        "origin": origin,
                        "classification_reason": "integration fixture",
                        "source_node_id": source_node_id,
                        "target_node_id": target_node_id,
                        "source_road_ids": [],
                        "classification_strategy": {"name": "test", "version": "1"},
                    },
                    geometry=_road_geometry(start=start, end=end),
                )
            )


def test_road_runs_generated_geojson_and_diagnostics_are_project_scoped() -> None:
    project_id = _create_project("Road viewport")
    run_id = _create_run(project_id=project_id, seed=41)
    empty_run_id = _create_run(project_id=project_id, seed=42)
    other_project_id = _create_project("Other road project")
    other_run_id = _create_run(project_id=other_project_id, seed=43)

    _insert_road(
        run_id=run_id,
        edge_id="edge-london-1",
        road_id="road-main",
        source_node_id="n1",
        target_node_id="n2",
        road_class="local",
        origin="growth_generated",
        length_m=100.0,
        start=(-0.13, 51.50),
        end=(-0.11, 51.51),
    )
    _insert_road(
        run_id=run_id,
        edge_id="edge-london-2",
        road_id="road-main",
        source_node_id="n2",
        target_node_id="n3",
        road_class="collector",
        origin="growth_generated",
        length_m=200.0,
        start=(-0.11, 51.51),
        end=(-0.09, 51.52),
    )
    _insert_road(
        run_id=run_id,
        edge_id="edge-paris",
        road_id="road-paris",
        source_node_id="n4",
        target_node_id="n5",
        road_class="local",
        origin="baseline_generated",
        length_m=50.0,
        start=(2.31, 48.85),
        end=(2.33, 48.86),
    )
    _insert_road(
        run_id=other_run_id,
        edge_id="edge-other",
        road_id="road-other",
        source_node_id="o1",
        target_node_id="o2",
        road_class="arterial",
        origin="baseline_generated",
        length_m=500.0,
        start=(-0.12, 51.50),
        end=(-0.08, 51.53),
    )

    client = TestClient(app)
    runs_response = client.get(f"/api/v1/projects/{project_id}/road-runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert {item["id"] for item in runs} == {str(run_id), str(empty_run_id)}
    counts = {item["id"]: item["generated_road_count"] for item in runs}
    assert counts[str(run_id)] == 3
    assert counts[str(empty_run_id)] == 0

    roads_response = client.get(
        f"/api/v1/projects/{project_id}/road-runs/{run_id}/roads/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert roads_response.status_code == 200
    body = roads_response.json()
    assert body["type"] == "FeatureCollection"
    assert body["project_id"] == str(project_id)
    assert body["run_id"] == str(run_id)
    assert body["geojson_crs"] == "EPSG:4326"
    assert body["working_srid"] == WORKING_SRID
    assert body["truncated"] is False
    assert len(body["features"]) == 2
    assert {feature["properties"]["road_class"] for feature in body["features"]} == {
        "collector",
        "local",
    }
    assert all(feature["geometry"]["type"] == "LineString" for feature in body["features"])

    diagnostics_response = client.get(
        f"/api/v1/projects/{project_id}/road-runs/{run_id}/diagnostics"
    )
    assert diagnostics_response.status_code == 200
    diagnostics = diagnostics_response.json()
    assert diagnostics["edge_count"] == 3
    assert diagnostics["road_count"] == 2
    assert diagnostics["node_count"] == 5
    assert diagnostics["component_count"] == 2
    assert diagnostics["dead_end_node_count"] == 4
    assert diagnostics["dead_end_ratio"] == pytest.approx(0.8)
    assert diagnostics["total_length_m"] == 350.0
    assert diagnostics["class_counts"] == {"collector": 1, "local": 2}

    mismatch = client.get(
        f"/api/v1/projects/{project_id}/road-runs/{other_run_id}/roads/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6"},
    )
    assert mismatch.status_code == 404

    missing_project = client.get(f"/api/v1/projects/{uuid.uuid4()}/road-runs")
    assert missing_project.status_code == 404


def test_generated_road_geojson_rejects_invalid_bbox() -> None:
    project_id = _create_project("Invalid road bbox")
    run_id = _create_run(project_id=project_id, seed=51)
    response = TestClient(app).get(
        f"/api/v1/projects/{project_id}/road-runs/{run_id}/roads/geojson",
        params={"bbox": "bad"},
    )
    assert response.status_code == 422
