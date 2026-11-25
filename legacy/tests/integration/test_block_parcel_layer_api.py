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
from backend.app.models.generated_entity import (
    GeneratedBlock,
    GeneratedParcel,
    GeneratedZone,
)
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


def _insert_block_parcel_fixture(
    *,
    run_id: uuid.UUID,
    block_key: str,
    parcel_key: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    geometry = _polygon(
        west=west,
        south=south,
        east=east,
        north=north,
    )
    inset = geometry.buffer(-max(geometry.length * 0.002, 0.1))

    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            zone = GeneratedZone(
                run_id=run_id,
                zone_class="residential",
                area_m2=float(geometry.area),
                attributes_json={},
                diagnostics_json={},
                geometry=from_shape(
                    MultiPolygon([geometry]),
                    srid=WORKING_SRID,
                    extended=True,
                ),
            )
            session.add(zone)
            session.flush()

            block = GeneratedBlock(
                run_id=run_id,
                block_key=block_key,
                zone_id=zone.id,
                area_m2=float(geometry.area),
                association_status="ASSOCIATED",
                attributes_json={
                    "zone_class": "residential",
                    "maximum_overlap_ratio": 1.0,
                    "subdivision": {
                        "parcel_count": 1,
                        "skip_reason": None,
                        "selected_frontage_road_id": "road:front",
                        "selected_frontage_length_m": 120.0,
                    },
                },
                geometry=from_shape(
                    geometry,
                    srid=WORKING_SRID,
                    extended=True,
                ),
            )
            session.add(block)
            session.flush()

            session.add(
                GeneratedParcel(
                    run_id=run_id,
                    parcel_key=parcel_key,
                    block_id=block.id,
                    zone_id=zone.id,
                    area_m2=float(geometry.area),
                    buildable_area_m2=float(inset.area),
                    frontage_m=120.0,
                    buildable_geometry=from_shape(
                        inset,
                        srid=WORKING_SRID,
                        extended=True,
                    ),
                    attributes_json={
                        "semantics": "planning_lot_non_cadastral",
                        "block_key": block_key,
                        "zone_class": "residential",
                        "buildable_ratio": float(inset.area / geometry.area),
                        "has_frontage": True,
                        "frontage_road_ids": ["road:front"],
                    },
                    geometry=from_shape(
                        geometry,
                        srid=WORKING_SRID,
                        extended=True,
                    ),
                )
            )


def test_block_parcel_runs_and_geojson_are_project_scoped() -> None:
    project_id = _create_project("Block parcel viewport")
    run_id = _create_run(project_id=project_id, seed=71)
    empty_run_id = _create_run(project_id=project_id, seed=72)
    other_project_id = _create_project("Other block parcel project")
    other_run_id = _create_run(project_id=other_project_id, seed=73)

    _insert_block_parcel_fixture(
        run_id=run_id,
        block_key="block:london",
        parcel_key="parcel:london:0000",
        west=-0.13,
        south=51.50,
        east=-0.11,
        north=51.52,
    )
    _insert_block_parcel_fixture(
        run_id=run_id,
        block_key="block:paris",
        parcel_key="parcel:paris:0000",
        west=2.31,
        south=48.85,
        east=2.33,
        north=48.87,
    )
    _insert_block_parcel_fixture(
        run_id=other_run_id,
        block_key="block:other",
        parcel_key="parcel:other:0000",
        west=-0.12,
        south=51.50,
        east=-0.10,
        north=51.52,
    )

    client = TestClient(app)
    runs_response = client.get(f"/api/v1/projects/{project_id}/block-runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert {item["id"] for item in runs} == {str(run_id), str(empty_run_id)}
    counts = {
        item["id"]: (
            item["generated_block_count"],
            item["generated_parcel_count"],
        )
        for item in runs
    }
    assert counts[str(run_id)] == (2, 2)
    assert counts[str(empty_run_id)] == (0, 0)

    blocks_response = client.get(
        f"/api/v1/projects/{project_id}/block-runs/{run_id}/blocks/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert blocks_response.status_code == 200
    blocks = blocks_response.json()
    assert blocks["type"] == "FeatureCollection"
    assert blocks["geojson_crs"] == "EPSG:4326"
    assert blocks["working_srid"] == WORKING_SRID
    assert blocks["truncated"] is False
    assert len(blocks["features"]) == 1
    block = blocks["features"][0]
    assert block["geometry"]["type"] == "Polygon"
    assert block["properties"]["block_key"] == "block:london"
    assert block["properties"]["association_status"] == "ASSOCIATED"
    assert block["properties"]["zone_class"] == "residential"
    assert block["properties"]["subdivision"]["parcel_count"] == 1

    parcels_response = client.get(
        f"/api/v1/projects/{project_id}/block-runs/{run_id}/parcels/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert parcels_response.status_code == 200
    parcels = parcels_response.json()
    assert parcels["truncated"] is False
    assert len(parcels["features"]) == 1
    parcel = parcels["features"][0]
    assert parcel["geometry"]["type"] == "Polygon"
    assert parcel["properties"]["parcel_key"] == "parcel:london:0000"
    assert parcel["properties"]["block_key"] == "block:london"
    assert parcel["properties"]["has_frontage"] is True
    assert parcel["properties"]["frontage_m"] == pytest.approx(120.0)
    assert 0.0 < parcel["properties"]["buildable_ratio"] < 1.0

    mismatch = client.get(
        f"/api/v1/projects/{project_id}/block-runs/{other_run_id}/blocks/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6"},
    )
    assert mismatch.status_code == 404

    missing_project = client.get(
        f"/api/v1/projects/{uuid.uuid4()}/block-runs"
    )
    assert missing_project.status_code == 404


def test_block_parcel_geojson_rejects_invalid_bbox() -> None:
    project_id = _create_project("Invalid block bbox")
    run_id = _create_run(project_id=project_id, seed=81)
    response = TestClient(app).get(
        f"/api/v1/projects/{project_id}/block-runs/{run_id}/blocks/geojson",
        params={"bbox": "bad"},
    )
    assert response.status_code == 422
