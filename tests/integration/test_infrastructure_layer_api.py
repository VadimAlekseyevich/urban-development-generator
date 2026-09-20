import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import Point, box
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generated_entity import GeneratedInfrastructure
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceFacility

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


def _point(lon: float, lat: float) -> object:
    x, y = _TO_WORKING.transform(lon, lat)
    return from_shape(Point(x, y), srid=WORKING_SRID, extended=True)


def _site(lon: float, lat: float) -> object:
    x, y = _TO_WORKING.transform(lon, lat)
    return from_shape(
        box(x - 20.0, y - 20.0, x + 20.0, y + 20.0),
        srid=WORKING_SRID,
        extended=True,
    )


def _seed() -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Infrastructure layer",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()

            dataset = Dataset(project_id=project.id, kind="facilities")
            session.add(dataset)
            session.flush()
            linked = DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="uploaded",
                source_metadata={},
            )
            unlinked = DatasetVersion(
                dataset_id=dataset.id,
                version=2,
                status="uploaded",
                source_metadata={},
            )
            session.add_all((linked, unlinked))
            session.flush()

            session.add_all(
                (
                    SourceFacility(
                        dataset_version_id=linked.id,
                        source_feature_id="existing-linked",
                        facility_class="school",
                        name="Existing school",
                        capacity=80.0,
                        attributes_json={"fixture": "linked"},
                        geometry=_point(-0.10, 51.51),
                    ),
                    SourceFacility(
                        dataset_version_id=unlinked.id,
                        source_feature_id="existing-unlinked",
                        facility_class="clinic",
                        name="Unlinked clinic",
                        capacity=50.0,
                        attributes_json={"fixture": "unlinked"},
                        geometry=_point(-0.09, 51.51),
                    ),
                )
            )
            session.flush()
            linked.status = "ready"
            unlinked.status = "ready"
            session.flush()

            run = GenerationRun(
                project_id=project.id,
                status="running",
                mode="EXPANSION",
                seed=42,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="test-v1",
            )
            run.dataset_versions.append(linked)
            session.add(run)
            session.flush()

            session.add(
                GeneratedInfrastructure(
                    id=uuid.uuid5(run.id, "generated-infrastructure:school:a"),
                    run_id=run.id,
                    geometry=_site(-0.08, 51.51),
                    candidate_id="school.general:parcel:parcel-a",
                    infrastructure_type_code="school.general",
                    category="education",
                    capacity=100.0,
                    acceptance_index=0,
                    geometry_kind="site",
                    site_area_m2=1600.0,
                    network_snapshot_id="roads:test-v1",
                    network_node_id="node-1",
                    network_snap_distance_m=3.0,
                    attributes_json={
                        "source_kind": "parcel",
                        "source_id": "parcel-a",
                    },
                )
            )
            session.flush()
            return project.id, run.id


def test_infrastructure_geojson_is_run_scoped_and_origin_explicit() -> None:
    project_id, run_id = _seed()
    client = TestClient(app)
    base = f"/api/v1/projects/{project_id}/infrastructure-runs"

    runs = client.get(base)
    assert runs.status_code == 200
    assert len(runs.json()) == 1
    assert runs.json()[0]["existing_facility_count"] == 1
    assert runs.json()[0]["generated_facility_count"] == 1

    response = client.get(
        f"{base}/{run_id}/facilities/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "FeatureCollection"
    assert body["project_id"] == str(project_id)
    assert body["run_id"] == str(run_id)
    assert body["geojson_crs"] == "EPSG:4326"
    assert body["working_srid"] == WORKING_SRID
    assert body["truncated"] is False
    assert len(body["features"]) == 2

    by_origin = {feature["origin"]: feature for feature in body["features"]}
    existing = by_origin["existing"]
    generated = by_origin["generated"]

    assert existing["properties"]["origin"] == "existing"
    assert existing["properties"]["source_feature_id"] == "existing-linked"
    assert existing["properties"]["facility_class"] == "school"
    assert existing["properties"]["capacity"] == 80.0
    assert existing["properties"]["fixture"] == "linked"

    assert generated["properties"]["origin"] == "generated"
    assert generated["properties"]["candidate_id"] == (
        "school.general:parcel:parcel-a"
    )
    assert generated["properties"]["infrastructure_type_code"] == "school.general"
    assert generated["properties"]["category"] == "education"
    assert generated["properties"]["capacity"] == 100.0
    assert generated["properties"]["geometry_kind"] == "site"
    assert generated["properties"]["network_node_id"] == "node-1"

    all_source_ids = {
        feature["properties"].get("source_feature_id")
        for feature in body["features"]
    }
    assert "existing-unlinked" not in all_source_ids

    existing_only = client.get(
        f"{base}/{run_id}/facilities/geojson",
        params={
            "bbox": "-0.2,51.45,0.0,51.6",
            "limit": 1,
            "origin": "existing",
        },
    )
    assert existing_only.status_code == 200
    assert existing_only.json()["truncated"] is False
    assert [item["origin"] for item in existing_only.json()["features"]] == [
        "existing"
    ]

    generated_only = client.get(
        f"{base}/{run_id}/facilities/geojson",
        params={
            "bbox": "-0.2,51.45,0.0,51.6",
            "limit": 1,
            "origin": "generated",
        },
    )
    assert generated_only.status_code == 200
    assert generated_only.json()["truncated"] is False
    assert [item["origin"] for item in generated_only.json()["features"]] == [
        "generated"
    ]


def test_infrastructure_geojson_is_bounded_and_validates_run_scope() -> None:
    project_id, run_id = _seed()
    client = TestClient(app)
    url = (
        f"/api/v1/projects/{project_id}/infrastructure-runs/{run_id}"
        "/facilities/geojson"
    )

    limited = client.get(
        url,
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 1},
    )
    assert limited.status_code == 200
    assert limited.json()["truncated"] is True
    assert len(limited.json()["features"]) == 1

    missing_run = client.get(
        (
            f"/api/v1/projects/{project_id}/infrastructure-runs/{uuid.uuid4()}"
            "/facilities/geojson"
        ),
        params={"bbox": "-0.2,51.45,0.0,51.6"},
    )
    assert missing_run.status_code == 404

    invalid_bbox = client.get(url, params={"bbox": "bad"})
    assert invalid_bbox.status_code == 422

    invalid_origin = client.get(
        url,
        params={"bbox": "-0.2,51.45,0.0,51.6", "origin": "other"},
    )
    assert invalid_origin.status_code == 422
