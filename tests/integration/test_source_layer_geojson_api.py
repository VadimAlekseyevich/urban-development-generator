import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import MultiLineString
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceRoad

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


def _create_dataset_versions(
    *,
    project_name: str,
    versions: tuple[int, ...] = (1,),
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name=project_name,
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            dataset = Dataset(project_id=project.id, kind="osm")
            session.add(dataset)
            session.flush()
            dataset_versions = [
                DatasetVersion(
                    dataset_id=dataset.id,
                    version=version,
                    status="processing",
                    source_metadata={"format": "test"},
                )
                for version in versions
            ]
            session.add_all(dataset_versions)
            session.flush()
            return project.id, [item.id for item in dataset_versions]


def _road_geometry(coordinates: list[tuple[float, float]]) -> object:
    projected = [_TO_WORKING.transform(lon, lat) for lon, lat in coordinates]
    return from_shape(
        MultiLineString([projected]),
        srid=WORKING_SRID,
        extended=True,
    )


def _insert_road(
    *,
    dataset_version_id: uuid.UUID,
    source_feature_id: str,
    coordinates: list[tuple[float, float]],
) -> None:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            session.add(
                SourceRoad(
                    dataset_version_id=dataset_version_id,
                    source_feature_id=source_feature_id,
                    road_class="local",
                    name=source_feature_id,
                    lanes=2,
                    max_speed_kph=50.0,
                    one_way=False,
                    attributes_json={"fixture": source_feature_id},
                    geometry=_road_geometry(coordinates),
                )
            )


def _mark_ready(*dataset_version_ids: uuid.UUID) -> None:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            for dataset_version_id in dataset_version_ids:
                version = session.get(DatasetVersion, dataset_version_id)
                assert version is not None
                version.status = "ready"


def test_bbox_geojson_endpoint_is_project_version_scoped_and_limited() -> None:
    project_id, versions = _create_dataset_versions(
        project_name="Viewport",
        versions=(1, 2),
    )
    version_id, other_version_id = versions
    other_project_id, other_project_versions = _create_dataset_versions(
        project_name="Other project"
    )
    other_project_version_id = other_project_versions[0]

    _insert_road(
        dataset_version_id=version_id,
        source_feature_id="inside-1",
        coordinates=[(-0.12, 51.50), (-0.10, 51.52)],
    )
    _insert_road(
        dataset_version_id=version_id,
        source_feature_id="inside-2",
        coordinates=[(-0.08, 51.49), (-0.05, 51.53)],
    )
    _insert_road(
        dataset_version_id=version_id,
        source_feature_id="outside",
        coordinates=[(2.30, 48.85), (2.31, 48.86)],
    )
    _insert_road(
        dataset_version_id=other_version_id,
        source_feature_id="other-version",
        coordinates=[(-0.11, 51.50), (-0.09, 51.51)],
    )
    _insert_road(
        dataset_version_id=other_project_version_id,
        source_feature_id="other-project",
        coordinates=[(-0.11, 51.50), (-0.09, 51.51)],
    )
    _mark_ready(version_id, other_version_id, other_project_version_id)

    client = TestClient(app)
    url = (
        f"/api/v1/projects/{project_id}/dataset-versions/{version_id}"
        "/source-layers/roads/geojson"
    )

    limited = client.get(
        url,
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 1},
    )
    assert limited.status_code == 200
    limited_body = limited.json()
    assert limited_body["type"] == "FeatureCollection"
    assert limited_body["project_id"] == str(project_id)
    assert limited_body["dataset_version_id"] == str(version_id)
    assert limited_body["layer"] == "roads"
    assert limited_body["query_bbox"] == [-0.2, 51.45, 0.0, 51.6]
    assert limited_body["geojson_crs"] == "EPSG:4326"
    assert limited_body["working_srid"] == WORKING_SRID
    assert limited_body["limit"] == 1
    assert limited_body["truncated"] is True
    assert len(limited_body["features"]) == 1

    complete = client.get(
        url,
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert complete.status_code == 200
    complete_body = complete.json()
    assert complete_body["truncated"] is False
    assert len(complete_body["features"]) == 2
    source_ids = {
        feature["properties"]["source_feature_id"]
        for feature in complete_body["features"]
    }
    assert source_ids == {"inside-1", "inside-2"}
    assert all(
        feature["geometry"]["type"] == "MultiLineString"
        for feature in complete_body["features"]
    )
    assert all(
        feature["properties"]["road_class"] == "local"
        for feature in complete_body["features"]
    )
    assert {
        feature["properties"]["attributes"]["fixture"]
        for feature in complete_body["features"]
    } == {"inside-1", "inside-2"}

    first_coordinate = complete_body["features"][0]["geometry"]["coordinates"][0][0]
    assert -0.2 <= first_coordinate[0] <= 0.0
    assert 51.45 <= first_coordinate[1] <= 51.6

    mismatch = client.get(
        (
            f"/api/v1/projects/{other_project_id}/dataset-versions/{version_id}"
            "/source-layers/roads/geojson"
        ),
        params={"bbox": "-0.2,51.45,0.0,51.6"},
    )
    assert mismatch.status_code == 404


@pytest.mark.parametrize(
    "bbox",
    [
        "bad",
        "-0.2,51.6,0.0,51.45",
        "-181,0,1,1",
    ],
)
def test_bbox_geojson_endpoint_rejects_invalid_bbox(bbox: str) -> None:
    project_id, versions = _create_dataset_versions(project_name=f"Invalid {bbox}")
    version_id = versions[0]
    response = TestClient(app).get(
        (
            f"/api/v1/projects/{project_id}/dataset-versions/{version_id}"
            "/source-layers/roads/geojson"
        ),
        params={"bbox": bbox},
    )
    assert response.status_code == 422


def test_bbox_geojson_endpoint_enforces_limit_cap() -> None:
    project_id, versions = _create_dataset_versions(project_name="Limit")
    version_id = versions[0]
    response = TestClient(app).get(
        (
            f"/api/v1/projects/{project_id}/dataset-versions/{version_id}"
            "/source-layers/roads/geojson"
        ),
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 5001},
    )
    assert response.status_code == 422
