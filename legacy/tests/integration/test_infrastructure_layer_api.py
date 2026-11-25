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
from backend.app.models.generated_entity import GeneratedBlock, GeneratedInfrastructure
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


def _seed(*, read_model: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
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
                metrics_json=(
                    {
                        "infrastructure": {
                            "read_model_version": "infrastructure-ui-v1",
                            "scenario_version": "scenario-v1",
                            "scenario_fingerprint": "a" * 64,
                            "raw_metrics": [
                                {
                                    "metric_id": (
                                        "infrastructure.population_coverage_ratio"
                                    ),
                                    "scalar_value": 0.7,
                                },
                                {
                                    "metric_id": (
                                        "infrastructure.age_specific_coverage"
                                    ),
                                    "age_coverage": [
                                        {
                                            "demographic_group": "child",
                                            "population": 100.0,
                                            "covered_population": 70.0,
                                            "coverage_ratio": 0.7,
                                        }
                                    ],
                                },
                                {
                                    "metric_id": (
                                        "infrastructure.network_distance_p50_m"
                                    ),
                                    "scalar_value": 300.0,
                                },
                                {
                                    "metric_id": (
                                        "infrastructure.network_distance_p90_m"
                                    ),
                                    "scalar_value": 500.0,
                                },
                                {
                                    "metric_id": "infrastructure.unmet_demand",
                                    "scalar_value": 3.0,
                                },
                                {
                                    "metric_id": (
                                        "infrastructure.capacity_utilization"
                                    ),
                                    "scalar_value": 0.75,
                                },
                            ],
                            "diagnostics": {
                                "infrastructure_type_count": 1,
                                "demand_item_count": 1,
                                "accepted_generated_facility_count": 1,
                                "existing_distance_sample_count": 1,
                                "generated_distance_sample_count": 1,
                            },
                            "facility_accessibility": {
                                "existing": [
                                    {
                                        "origin": "existing",
                                        "facility_id": "facility-a",
                                        "source_ref": "dataset:facilities-v1",
                                        "source_feature_id": "existing-linked",
                                        "infrastructure_type_code": (
                                            "school.general"
                                        ),
                                        "capacity": 80.0,
                                        "max_network_distance_m": 1000.0,
                                        "reachable_demand_count": 1,
                                        "nearest_distance_m": 100.0,
                                        "farthest_distance_m": 100.0,
                                    }
                                ],
                                "generated": [
                                    {
                                        "origin": "generated",
                                        "candidate_id": (
                                            "school.general:parcel:parcel-a"
                                        ),
                                        "infrastructure_type_code": (
                                            "school.general"
                                        ),
                                        "acceptance_index": 0,
                                        "network_snapshot_id": "roads:test-v1",
                                        "max_network_distance_m": 1000.0,
                                        "reachable_demand_count": 1,
                                        "nearest_distance_m": 300.0,
                                        "farthest_distance_m": 300.0,
                                    }
                                ],
                            },
                        }
                    }
                    if read_model
                    else None
                ),
            )
            run.dataset_versions.append(linked)
            session.add(run)
            session.flush()

            if read_model:
                session.add(
                    GeneratedBlock(
                        run_id=run.id,
                        block_key="block:london",
                        area_m2=1600.0,
                        association_status="ASSOCIATED",
                        attributes_json={
                            "infrastructure": {
                                "read_model_version": "infrastructure-ui-v1",
                                "scenario_version": "scenario-v1",
                                "scenario_fingerprint": "a" * 64,
                                "gross_demand": 10.0,
                                "served_demand": 7.0,
                                "final_unmet_demand": 3.0,
                                "coverage_ratio": 0.7,
                                "demands": [
                                    {
                                        "infrastructure_type_code": (
                                            "school.general"
                                        ),
                                        "category": "education",
                                        "gross_demand": 10.0,
                                        "final_unmet_demand": 3.0,
                                    }
                                ],
                            }
                        },
                        geometry=_site(-0.12, 51.51),
                    )
                )

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


def test_infrastructure_metrics_and_unmet_demand_read_model() -> None:
    project_id, run_id = _seed()
    client = TestClient(app)
    base = f"/api/v1/projects/{project_id}/infrastructure-runs/{run_id}"

    metrics_response = client.get(f"{base}/metrics")
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["read_model_version"] == "infrastructure-ui-v1"
    assert metrics["scenario_version"] == "scenario-v1"
    raw_by_id = {item["metric_id"]: item for item in metrics["raw_metrics"]}
    assert raw_by_id["infrastructure.unmet_demand"]["scalar_value"] == pytest.approx(
        3.0
    )
    assert raw_by_id[
        "infrastructure.population_coverage_ratio"
    ]["scalar_value"] == pytest.approx(0.7)

    by_origin = {
        item["origin"]: item for item in metrics["facility_accessibility"]
    }
    assert by_origin["existing"]["source_feature_id"] == "existing-linked"
    assert by_origin["existing"]["nearest_distance_m"] == pytest.approx(100.0)
    assert by_origin["generated"]["candidate_id"] == (
        "school.general:parcel:parcel-a"
    )
    assert by_origin["generated"]["reachable_demand_count"] == 1

    demand_response = client.get(
        f"{base}/demand/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6", "limit": 10},
    )
    assert demand_response.status_code == 200
    demand = demand_response.json()
    assert demand["type"] == "FeatureCollection"
    assert demand["truncated"] is False
    assert len(demand["features"]) == 1
    properties = demand["features"][0]["properties"]
    assert properties["block_key"] == "block:london"
    assert properties["gross_demand"] == pytest.approx(10.0)
    assert properties["served_demand"] == pytest.approx(7.0)
    assert properties["final_unmet_demand"] == pytest.approx(3.0)
    assert properties["coverage_ratio"] == pytest.approx(0.7)


def test_infrastructure_read_model_not_ready_is_distinct_from_missing_run() -> None:
    project_id, run_id = _seed(read_model=False)
    client = TestClient(app)
    base = f"/api/v1/projects/{project_id}/infrastructure-runs/{run_id}"

    metrics_response = client.get(f"{base}/metrics")
    assert metrics_response.status_code == 409

    demand_response = client.get(
        f"{base}/demand/geojson",
        params={"bbox": "-0.2,51.45,0.0,51.6"},
    )
    assert demand_response.status_code == 409
