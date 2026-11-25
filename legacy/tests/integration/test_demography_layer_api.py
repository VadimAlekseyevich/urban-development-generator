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
from backend.app.models.generated_entity import GeneratedBlock, GeneratedZone
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


def _create_demography_fixture() -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Demography viewport",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()

            run = GenerationRun(
                project_id=project.id,
                status="running",
                mode="EXPANSION",
                seed=91,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="1",
                metrics_json={
                    "demography": {
                        "scenario_version": "demography-v1",
                        "scenario_fingerprint": "a" * 64,
                        "employment_config_version": "employment-v1",
                        "employment_config_fingerprint": "b" * 64,
                        "block_count": 2,
                        "area_m2": 500_000.0,
                        "population": 100,
                        "population_density_per_km2": 200.0,
                        "jobs_estimate": 25.0,
                        "age_groups": [
                            {
                                "code": "child",
                                "min_age": 0,
                                "max_age": 17,
                                "residents": 30,
                                "share": 0.3,
                            },
                            {
                                "code": "adult",
                                "min_age": 18,
                                "max_age": None,
                                "residents": 70,
                                "share": 0.7,
                            },
                        ],
                    }
                },
            )
            session.add(run)
            session.flush()

            london = _polygon(
                west=-0.13,
                south=51.50,
                east=-0.11,
                north=51.52,
            )
            paris = _polygon(
                west=2.31,
                south=48.85,
                east=2.33,
                north=48.87,
            )
            zone = GeneratedZone(
                run_id=run.id,
                zone_class="residential",
                area_m2=float(london.area + paris.area),
                attributes_json={},
                diagnostics_json={},
                geometry=from_shape(
                    MultiPolygon([london, paris]),
                    srid=WORKING_SRID,
                    extended=True,
                ),
            )
            session.add(zone)
            session.flush()

            for key, geometry, population, density, jobs, children, adults in (
                ("block:london", london, 40, 180.0, 10.0, 10, 30),
                ("block:paris", paris, 60, 220.0, 15.0, 20, 40),
            ):
                session.add(
                    GeneratedBlock(
                        run_id=run.id,
                        block_key=key,
                        zone_id=zone.id,
                        area_m2=float(geometry.area),
                        association_status="ASSOCIATED",
                        attributes_json={
                            "zone_class": "residential",
                            "demography": {
                                "population": population,
                                "population_density_per_km2": density,
                                "jobs_estimate": jobs,
                                "age_groups": [
                                    {
                                        "code": "child",
                                        "min_age": 0,
                                        "max_age": 17,
                                        "residents": children,
                                        "share": children / population,
                                    },
                                    {
                                        "code": "adult",
                                        "min_age": 18,
                                        "max_age": None,
                                        "residents": adults,
                                        "share": adults / population,
                                    },
                                ],
                            },
                        },
                        geometry=from_shape(
                            geometry,
                            srid=WORKING_SRID,
                            extended=True,
                        ),
                    )
                )
            return project.id, run.id


def test_demography_metrics_and_choropleth_are_project_scoped() -> None:
    project_id, run_id = _create_demography_fixture()
    client = TestClient(app)

    runs_response = client.get(
        f"/api/v1/projects/{project_id}/demography-runs"
    )
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert len(runs) == 1
    assert runs[0]["id"] == str(run_id)
    assert runs[0]["population"] == 100
    assert runs[0]["population_density_per_km2"] == pytest.approx(200.0)
    assert runs[0]["jobs_estimate"] == pytest.approx(25.0)

    metrics_response = client.get(
        f"/api/v1/projects/{project_id}/demography-runs/{run_id}/metrics"
    )
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["scenario_version"] == "demography-v1"
    assert metrics["block_count"] == 2
    assert metrics["population"] == 100
    assert metrics["age_groups"][0]["code"] == "child"
    assert metrics["age_groups"][0]["share"] == pytest.approx(0.3)

    blocks_response = client.get(
        f"/api/v1/projects/{project_id}/demography-runs/{run_id}/blocks/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert blocks_response.status_code == 200
    blocks = blocks_response.json()
    assert blocks["type"] == "FeatureCollection"
    assert blocks["geojson_crs"] == "EPSG:4326"
    assert blocks["truncated"] is False
    assert len(blocks["features"]) == 1
    feature = blocks["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"]["block_key"] == "block:london"
    assert feature["properties"]["population"] == 40
    assert feature["properties"]["population_density_per_km2"] == pytest.approx(
        180.0
    )
    assert feature["properties"]["jobs_estimate"] == pytest.approx(10.0)
    assert feature["properties"]["age_groups"][0]["share"] == pytest.approx(0.25)

    mismatch = client.get(
        f"/api/v1/projects/{uuid.uuid4()}/demography-runs/{run_id}/metrics"
    )
    assert mismatch.status_code == 404


def test_demography_geojson_rejects_invalid_bbox() -> None:
    project_id, run_id = _create_demography_fixture()
    response = TestClient(app).get(
        f"/api/v1/projects/{project_id}/demography-runs/{run_id}/blocks/geojson",
        params={"bbox": "bad"},
    )
    assert response.status_code == 422
