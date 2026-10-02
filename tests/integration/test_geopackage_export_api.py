from __future__ import annotations

import json
import uuid

import pyogrio
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import MultiLineString, MultiPolygon, Polygon
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from backend.app.adapters import LocalArtifactStore
from backend.app.api.dependencies import get_artifact_store
from backend.app.application.geopackage_exports import (
    GEOPACKAGE_EXPORT_JOB_TYPE,
    GeoPackageExportJobService,
    GeoPackageExportRunStatus,
)
from backend.app.db.geopackage_export_page_reader import (
    SqlAlchemyGeoPackageExportPageReader,
)
from backend.app.db.geopackage_export_repository import (
    SqlAlchemyGeoPackageExportRepository,
)
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.geopackage_export import GeoPackageExport
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceBuilding, SourceRoad
from backend.app.services.geopackage_export import StreamingGeoPackageExportPipeline

WORKING_SRID = 3857
_TO_WORKING = Transformer.from_crs(4326, WORKING_SRID, always_xy=True)
BBOX = [-0.2, 51.45, 0.0, 51.6]
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _line(points: list[tuple[float, float]]) -> object:
    return from_shape(
        MultiLineString([[_TO_WORKING.transform(*point) for point in points]]),
        srid=WORKING_SRID,
        extended=True,
    )


def _polygon(points: list[tuple[float, float]]) -> object:
    transformed = [_TO_WORKING.transform(*point) for point in points]
    return from_shape(
        MultiPolygon([Polygon(transformed)]),
        srid=WORKING_SRID,
        extended=True,
    )


def _fixture(*, ready: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    with SessionFactory() as session:
        with session.begin():
            project = Project(
                name="GeoPackage export",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            dataset = Dataset(project_id=project.id, kind="mixed")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="processing",
                source_metadata={"format": "fixture"},
            )
            session.add(version)
            session.flush()
            if ready:
                session.add_all(
                    [
                        SourceRoad(
                            dataset_version_id=version.id,
                            source_feature_id="road-a",
                            road_class="local",
                            attributes_json={"fixture": 1},
                            geometry=_line([(-0.12, 51.49), (-0.10, 51.51)]),
                        ),
                        SourceRoad(
                            dataset_version_id=version.id,
                            source_feature_id="road-b",
                            road_class="collector",
                            attributes_json={"fixture": 2},
                            geometry=_line([(-0.11, 51.48), (-0.09, 51.50)]),
                        ),
                        SourceBuilding(
                            dataset_version_id=version.id,
                            source_feature_id="building-a",
                            building_class="residential",
                            levels=4,
                            attributes_json={"fixture": 3},
                            geometry=_polygon(
                                [
                                    (-0.105, 51.495),
                                    (-0.104, 51.495),
                                    (-0.104, 51.496),
                                    (-0.105, 51.496),
                                    (-0.105, 51.495),
                                ]
                            ),
                        ),
                    ]
                )
                session.flush()
                version.status = "ready"
            return project.id, version.id


def _payload(version_id: uuid.UUID) -> dict[str, object]:
    return {
        "layer_ids": ["source.roads", "source.buildings"],
        "dataset_version_id": str(version_id),
        "run_id": None,
        "bbox": BBOX,
        "max_features_per_layer": 10,
        "max_total_features": 20,
    }


def _worker_service(store: LocalArtifactStore) -> GeoPackageExportJobService:
    return GeoPackageExportJobService(
        repository=SqlAlchemyGeoPackageExportRepository(
            session_factory=SessionFactory,
        ),
        pipeline=StreamingGeoPackageExportPipeline(
            store=store,
            page_reader=SqlAlchemyGeoPackageExportPageReader(
                session_factory=SessionFactory,
            ),
        ),
    )


def test_api_outbox_worker_multilayer_artifact_and_download_are_one_flow(
    tmp_path,
) -> None:
    project_id, version_id = _fixture()
    foreign_project_id, _foreign_version = _fixture()
    store = LocalArtifactStore(tmp_path / "storage")
    app.dependency_overrides[get_artifact_store] = lambda: store

    url = f"/api/v1/projects/{project_id}/exports/geopackage"
    created = client.post(url, json=_payload(version_id))
    assert created.status_code == 202, created.text
    state = created.json()
    assert state["schema_version"] == "geopackage-export-v1"
    assert state["status"] == "queued"
    assert state["layer_ids"] == ["source.buildings", "source.roads"]
    assert state["dataset_version_id"] == str(version_id)
    assert state["run_id"] is None
    assert state["artifact"] is None
    assert state["layer_counts"] is None
    job_id = uuid.UUID(state["job_id"])

    with SessionFactory() as session:
        job = session.get(Job, job_id)
        export = session.get(GeoPackageExport, job_id)
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == job_id))
        before = (
            session.scalar(select(func.count()).select_from(Job)),
            session.scalar(select(func.count()).select_from(GeoPackageExport)),
            session.scalar(select(func.count()).select_from(JobOutbox)),
        )
        assert job is not None and job.job_type == GEOPACKAGE_EXPORT_JOB_TYPE
        assert export is not None
        assert export.layer_ids_json == ["source.buildings", "source.roads"]
        assert outbox is not None and outbox.queue_name == "export"
        assert outbox.payload == {
            "task": "run_geopackage_export",
            "job_id": str(job_id),
        }

    duplicate_payload = _payload(version_id)
    duplicate_payload["layer_ids"] = ["source.buildings", "source.roads"]
    duplicate = client.post(url, json=duplicate_payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["job_id"] == str(job_id)
    with SessionFactory() as session:
        after = (
            session.scalar(select(func.count()).select_from(Job)),
            session.scalar(select(func.count()).select_from(GeoPackageExport)),
            session.scalar(select(func.count()).select_from(JobOutbox)),
        )
        assert after == before

    result = _worker_service(store).run(job_id=job_id)
    assert result.status is GeoPackageExportRunStatus.SUCCEEDED
    assert result.total_feature_count == 3

    status_response = client.get(f"{url}/{job_id}")
    assert status_response.status_code == 200
    completed = status_response.json()
    assert completed["status"] == "succeeded"
    assert completed["attempt_count"] == 1
    assert completed["total_feature_count"] == 3
    assert completed["layer_counts"] == [
        {"layer_id": "source.buildings", "feature_count": 1},
        {"layer_id": "source.roads", "feature_count": 2},
    ]
    assert completed["artifact"]["content_type"] == "application/geopackage+sqlite3"
    assert completed["artifact"]["filename"].startswith("urban-layers-")

    download = client.get(f"{url}/{job_id}/download")
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith(
        "application/geopackage+sqlite3"
    )
    assert "attachment; filename=" in download.headers["content-disposition"]

    local = tmp_path / "download.gpkg"
    local.write_bytes(download.content)
    assert {str(row[0]) for row in pyogrio.list_layers(local)} == {
        "source_buildings",
        "source_roads",
    }
    roads = pyogrio.read_dataframe(local, layer="source_roads")
    assert {
        json.loads(value)["source_feature_id"]
        for value in roads["properties_json"]
    } == {"road-a", "road-b"}
    buildings = pyogrio.read_dataframe(local, layer="source_buildings")
    assert json.loads(buildings["properties_json"].iloc[0])[
        "source_feature_id"
    ] == "building-a"

    with SessionFactory() as session:
        job = session.get(Job, job_id)
        export = session.get(GeoPackageExport, job_id)
        assert job is not None and job.status == "succeeded"
        assert export is not None and export.artifact_id is not None
        artifact = session.get(Artifact, export.artifact_id)
        assert artifact is not None
        assert artifact.state == ArtifactLifecycleState.REFERENCED.value
        assert artifact.owner_type == "job"
        assert artifact.owner_id == job_id

    repeated = _worker_service(store).run(job_id=job_id)
    assert repeated.status is GeoPackageExportRunStatus.ALREADY_SUCCEEDED
    assert client.get(
        f"/api/v1/projects/{foreign_project_id}/exports/geopackage/{job_id}"
    ).status_code == 404


def test_create_rejects_unpublished_owner_and_non_table_catalog_layer() -> None:
    project_id, unready_version = _fixture(ready=False)
    base = f"/api/v1/projects/{project_id}/exports/geopackage"

    unavailable = client.post(base, json=_payload(unready_version))
    assert unavailable.status_code == 409, unavailable.text

    invalid = client.post(
        base,
        json={
            "layer_ids": ["validation.violations"],
            "run_id": str(uuid.uuid4()),
            "bbox": BBOX,
            "max_features_per_layer": 10,
            "max_total_features": 20,
        },
    )
    assert invalid.status_code == 422, invalid.text
