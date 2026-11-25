import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
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


def _project_boundary() -> object:
    ring_wgs84 = [
        (-0.15, 51.49),
        (-0.05, 51.49),
        (-0.05, 51.55),
        (-0.15, 51.55),
        (-0.15, 51.49),
    ]
    ring_working = [
        _TO_WORKING.transform(longitude, latitude)
        for longitude, latitude in ring_wgs84
    ]
    return from_shape(
        MultiPolygon([Polygon(ring_working)]),
        srid=WORKING_SRID,
        extended=True,
    )


def _create_project(*, with_boundary: bool) -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Boundary map fixture",
                working_srid=WORKING_SRID,
                boundary=_project_boundary() if with_boundary else None,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            return project.id


def test_project_boundary_geojson_is_transformed_for_map_display() -> None:
    project_id = _create_project(with_boundary=True)

    response = TestClient(app).get(f"/api/v1/projects/{project_id}/boundary/geojson")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "Feature"
    assert body["id"] == str(project_id)
    assert body["properties"] == {
        "project_id": str(project_id),
        "working_srid": WORKING_SRID,
        "geojson_crs": "EPSG:4326",
    }
    assert body["geometry"]["type"] == "MultiPolygon"
    longitude, latitude = body["geometry"]["coordinates"][0][0][0]
    assert longitude == pytest.approx(-0.15, abs=1e-6)
    assert latitude == pytest.approx(51.49, abs=1e-6)


def test_project_boundary_geojson_preserves_empty_boundary_and_404_scope() -> None:
    project_id = _create_project(with_boundary=False)
    client = TestClient(app)

    empty = client.get(f"/api/v1/projects/{project_id}/boundary/geojson")
    assert empty.status_code == 200
    assert empty.json()["geometry"] is None

    missing = client.get(f"/api/v1/projects/{uuid.uuid4()}/boundary/geojson")
    assert missing.status_code == 404
