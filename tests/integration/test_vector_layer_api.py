"""S13-T02 PostGIS acceptance: source/run owner isolation, keyset and GIS policy."""

import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generated_entity import GeneratedBlock, GeneratedRoad
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceFacility, SourceRoad

WORKING_SRID = 3857
_TO_WORKING = Transformer.from_crs(4326, WORKING_SRID, always_xy=True)
BBOX = "-0.2,51.45,0.0,51.6"
client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _line(points: list[tuple[float, float]]) -> object:
    return from_shape(
        MultiLineString([[_TO_WORKING.transform(*point) for point in points]]),
        srid=WORKING_SRID,
        extended=True,
    )


def _generated_line(points: list[tuple[float, float]]) -> object:
    return from_shape(
        LineString([_TO_WORKING.transform(*point) for point in points]),
        srid=WORKING_SRID,
        extended=True,
    )


def _block() -> object:
    return from_shape(
        Polygon(
            [
                _TO_WORKING.transform(-0.12, 51.49),
                _TO_WORKING.transform(-0.10, 51.49),
                _TO_WORKING.transform(-0.10, 51.51),
                _TO_WORKING.transform(-0.12, 51.51),
            ]
        ),
        srid=WORKING_SRID,
        extended=True,
    )


def _fixture() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Vector viewport",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            other_project = Project(
                name="Foreign viewport",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add_all([project, other_project])
            session.flush()
            dataset = Dataset(project_id=project.id, kind="osm")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id, version=1, status="processing",
                source_metadata={"format": "fixture"},
            )
            session.add(version)
            session.flush()
            run = GenerationRun(
                project_id=project.id,
                status="queued",
                mode="EXPANSION",
                seed=7,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="1",
                commit_sha="a" * 40,
            )
            session.add(run)
            session.flush()
            return project.id, version.id, run.id, other_project.id


def _url(project: uuid.UUID, layer: str) -> str:
    return f"/api/v1/projects/{project}/vector-layers/{layer}/geojson"


def test_source_geojson_keyset_is_bounded_project_version_scoped_and_render_only() -> None:
    project, version, run, foreign_project = _fixture()
    zigzag = [
        (-0.12, 51.50),
        (-0.119, 51.5001),
        (-0.118, 51.50),
        (-0.117, 51.5001),
        (-0.116, 51.50),
        (-0.115, 51.5001),
        (-0.114, 51.50),
    ]
    with Session(engine) as session:
        with session.begin():
            roads = [
                SourceRoad(
                    dataset_version_id=version,
                    source_feature_id=f"inside-{number}",
                    road_class="local",
                    attributes_json={"input": number},
                    geometry=_line(zigzag),
                )
                for number in (1, 2, 3)
            ]
            outside = SourceRoad(
                dataset_version_id=version,
                source_feature_id="outside",
                road_class="local",
                geometry=_line([(2.3, 48.85), (2.31, 48.86)]),
            )
            session.add_all([*roads, outside])
            item = session.get(DatasetVersion, version)
            assert item is not None
            item.status = "ready"

    base_params = {"dataset_version_id": str(version), "bbox": BBOX}
    url = _url(project, "source.roads")
    whole = client.get(url, params={**base_params, "limit": 5})
    assert whole.status_code == 200, whole.text
    whole_data = whole.json()
    assert whole_data["schema_version"] == "bounded-vector-v1"
    assert whole_data["definition_version"] == "1"
    assert whole_data["type"] == "FeatureCollection"
    assert whole_data["presentation_crs"] == "EPSG:4326"
    assert whole_data["query_bbox"] == [-0.2, 51.45, 0.0, 51.6]
    assert whole_data["working_srid"] == WORKING_SRID
    assert whole_data["dataset_version_id"] == str(version)
    assert whole_data["run_id"] is None
    assert len(whole_data["features"]) == 3
    assert whole_data["truncated"] is False
    assert whole_data["next_after"] is None
    ids = [item["id"] for item in whole_data["features"]]
    assert ids == sorted(ids)
    assert {
        item["properties"]["source_feature_id"]
        for item in whole_data["features"]
    } == {"inside-1", "inside-2", "inside-3"}
    assert {
        item["properties"]["attributes"]["input"]
        for item in whole_data["features"]
    } == {1, 2, 3}

    first = client.get(url, params={**base_params, "limit": 1}).json()
    second = client.get(
        url, params={**base_params, "limit": 1, "after": first["next_after"]},
    ).json()
    last = client.get(
        url, params={**base_params, "limit": 1, "after": second["next_after"]},
    ).json()
    assert [first["features"][0]["id"], second["features"][0]["id"],
            last["features"][0]["id"]] == ids
    assert first["truncated"] is second["truncated"] is True
    assert first["next_after"] == ids[0] and second["next_after"] == ids[1]
    assert last["truncated"] is False and last["next_after"] is None
    assert client.get(url, params={**base_params, "after": ids[-1]}).json()[
        "features"
    ] == []

    simplified = client.get(
        url, params={**base_params, "simplify_m": 30},
    )
    assert simplified.status_code == 200, simplified.text
    s = simplified.json()
    assert s["simplify_m"] == 30
    assert [f["id"] for f in s["features"]] == ids
    assert len(s["features"][0]["geometry"]["coordinates"][0]) < len(zigzag)
    # The opt-in generalization never replaces immutable canonical source data.
    again = client.get(url, params=base_params).json()
    assert again["features"] == whole_data["features"]

    wrong = client.get(_url(foreign_project, "source.roads"), params=base_params)
    assert wrong.status_code == 404
    assert client.get(
        url, params={**base_params, "dataset_version_id": str(uuid.uuid4())},
    ).status_code == 404
    assert client.get(
        url, params={**base_params, "run_id": str(run)},
    ).status_code == 422


def test_run_keyset_uses_exact_run_owner_and_derived_read_model_status() -> None:
    project, version, run, foreign_project = _fixture()
    with Session(engine) as session:
        with session.begin():
            session.add_all(
                [
                    GeneratedRoad(
                        run_id=run,
                        attributes_json={"road_class": "local", "origin": "generated"},
                        geometry=_generated_line([(-0.12, 51.49), (-0.10, 51.51)]),
                    ),
                    GeneratedBlock(
                        run_id=run,
                        attributes_json={
                            "demography": {
                                "population": 23,
                                "population_density_per_km2": 12.5,
                                "jobs_estimate": 6.5,
                                "age_groups": [],
                            },
                            "infrastructure": {"demand": 5},
                        },
                        geometry=_block(),
                    ),
                ]
            )

    road_params = {"run_id": str(run), "bbox": BBOX}
    generated = client.get(_url(project, "generated.roads"), params=road_params)
    assert generated.status_code == 200, generated.text
    assert len(generated.json()["features"]) == 1
    assert generated.json()["features"][0]["properties"]["road_class"] == "local"
    assert generated.json()["run_id"] == str(run)
    assert generated.json()["dataset_version_id"] is None
    assert client.get(
        _url(foreign_project, "generated.roads"), params=road_params,
    ).status_code == 404
    assert client.get(
        _url(project, "generated.roads"),
        params={**road_params, "run_id": str(uuid.uuid4())},
    ).status_code == 404
    assert client.get(
        _url(project, "generated.roads"),
        params={**road_params, "dataset_version_id": str(version)},
    ).status_code == 422

    for layer in ("generated.demography", "generated.infrastructure_demand"):
        missing = client.get(_url(project, layer), params=road_params)
        assert missing.status_code == 409, missing.text

    with Session(engine) as session:
        with session.begin():
            stored = session.get(GenerationRun, run)
            assert stored is not None
            stored.metrics_json = {
                "demography": {"population": 23},
                "infrastructure": {"read_model_version": "1"},
            }

    demo = client.get(_url(project, "generated.demography"), params=road_params)
    assert demo.status_code == 200, demo.text
    assert demo.json()["features"][0]["properties"]["population"] == 23
    demand = client.get(
        _url(project, "generated.infrastructure_demand"), params=road_params,
    )
    assert demand.status_code == 200, demand.text
    assert demand.json()["features"][0]["properties"]["demand"] == 5

    # Existing facilities bind to the run's exact linked dataset-version set.
    with Session(engine) as session:
        with session.begin():
            source = SourceFacility(
                dataset_version_id=version,
                source_feature_id="school",
                facility_class="school",
                attributes_json={"scope": "fixture"},
                geometry=from_shape(
                    Point(_TO_WORKING.transform(-0.11, 51.5)),
                    srid=WORKING_SRID,
                    extended=True,
                ),
            )
            session.add(source)
            stored = session.get(GenerationRun, run)
            item = session.get(DatasetVersion, version)
            assert stored is not None and item is not None
            stored.dataset_versions.append(item)
    existing = client.get(
        _url(project, "run.existing_facilities"), params=road_params,
    )
    assert existing.status_code == 200, existing.text
    assert len(existing.json()["features"]) == 1
    assert existing.json()["features"][0]["properties"]["origin"] == "existing"
    assert existing.json()["features"][0]["properties"]["dataset_version_id"] == str(
        version
    )


@pytest.mark.parametrize(
    "layer, extra, expected",
    (
        ("source.roads", {}, 422),
        ("generated.roads", {}, 422),
        ("analysis.suitability", {}, 422),
        ("project.boundary", {}, 422),
        ("validation.violations", {}, 422),
        ("source.nonexistent", {}, 404),
    ),
)
def test_canonical_scope_and_excluded_non_tabular_routes(
    layer: str,
    extra: dict[str, object],
    expected: int,
) -> None:
    project, _version, _run, _foreign = _fixture()
    assert client.get(
        _url(project, layer), params={"bbox": BBOX, **extra},
    ).status_code == expected


@pytest.mark.parametrize(
    "extra",
    (
        {"bbox": "0,0,0,1"},
        {"bbox": "-181,0,1,1"},
        {"after": "bad"},
        {"limit": 5001},
        {"limit": 0},
        {"presentation_srid": 3857},
        {"simplify_m": 100.01},
        {"simplify_m": -0.01},
        {"simplify_m": "nan"},
        {"simplify_m": "inf"},
    ),
)
def test_vector_endpoint_rejects_bad_bbox_cursor_limits_crs_and_tolerance(
    extra: dict[str, object],
) -> None:
    project, version, _run, _foreign = _fixture()
    assert client.get(
        _url(project, "source.roads"),
        params={"bbox": BBOX, "dataset_version_id": str(version), **extra},
    ).status_code == 422
