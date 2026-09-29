"""S13-T03 real PostGIS MVT: bounded binary content and exact owner isolation."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generated_entity import GeneratedBlock, GeneratedRoad
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceFacility, SourceRoad

PROJECT_CRS = 3857
_TO_PROJECT = Transformer.from_crs(4326, PROJECT_CRS, always_xy=True)
Z, X, Y = 10, 511, 340  # London; -0.11, 51.5 falls strictly inside the tile.
URL = f"/tiles/{Z}/{X}/{Y}.mvt"
client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _road(*, source: bool, outside: bool = False) -> object:
    points = (
        [(2.3, 48.85), (2.31, 48.86)]
        if outside else [(-0.12, 51.50), (-0.11, 51.51)]
    )
    coords = [_TO_PROJECT.transform(*point) for point in points]
    geometry = MultiLineString([coords]) if source else LineString(coords)
    return from_shape(geometry, srid=PROJECT_CRS, extended=True)


def _facility() -> object:
    return from_shape(
        Point(_TO_PROJECT.transform(-0.11, 51.5)),
        srid=PROJECT_CRS,
        extended=True,
    )


def _block() -> object:
    return from_shape(
        Polygon(
            [
                _TO_PROJECT.transform(-0.12, 51.49),
                _TO_PROJECT.transform(-0.10, 51.49),
                _TO_PROJECT.transform(-0.10, 51.51),
                _TO_PROJECT.transform(-0.12, 51.51),
            ]
        ),
        srid=PROJECT_CRS,
        extended=True,
    )


def _fixture() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="MVT project", working_srid=PROJECT_CRS, boundary_metadata={},
            )
            foreign = Project(
                name="Other MVT project", working_srid=PROJECT_CRS,
                boundary_metadata={},
            )
            session.add_all([project, foreign])
            session.flush()
            dataset = Dataset(project_id=project.id, kind="osm")
            session.add(dataset)
            session.flush()
            a = DatasetVersion(
                dataset_id=dataset.id, version=1, status="processing",
                source_metadata={"format": "fixture"},
            )
            b = DatasetVersion(
                dataset_id=dataset.id, version=2, status="processing",
                source_metadata={"format": "fixture"},
            )
            run = GenerationRun(
                project_id=project.id, mode="EXPANSION", status="queued",
                seed=9, working_srid=PROJECT_CRS, config_json={},
                config_schema_version="1", commit_sha="a" * 40,
            )
            session.add_all([a, b, run])
            session.flush()
            run.dataset_versions.append(a)
            return project.id, foreign.id, a.id, b.id, run.id


def _endpoint(project_id: uuid.UUID, layer: str) -> str:
    return f"/api/v1/projects/{project_id}/vector-layers/{layer}{URL}"


def _varint(blob: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(blob):
        item = blob[pos]
        pos += 1
        result |= (item & 0x7f) << shift
        if item < 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise AssertionError("invalid protobuf varint")
    raise AssertionError("truncated protobuf varint")


def _fields(blob: bytes) -> list[tuple[int, int, bytes | int]]:
    """Read just the protobuf wire envelope, without a new runtime dependency."""
    result: list[tuple[int, int, bytes | int]] = []
    pos = 0
    while pos < len(blob):
        tag, pos = _varint(blob, pos)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = _varint(blob, pos)
            result.append((field, wire, value))
        elif wire == 2:
            size, pos = _varint(blob, pos)
            assert pos + size <= len(blob)
            result.append((field, wire, blob[pos:pos + size]))
            pos += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            assert pos + size <= len(blob)
            result.append((field, wire, blob[pos:pos + size]))
            pos += size
        else:
            raise AssertionError(f"unexpected protobuf wire type {wire}")
    return result


def _features(blob: bytes) -> tuple[str, list[bytes], set[str]]:
    layers = [v for field, wire, v in _fields(blob) if field == 3 and wire == 2]
    assert len(layers) == 1
    layer = layers[0]
    assert isinstance(layer, bytes)
    items = _fields(layer)
    names = [v.decode() for f, w, v in items if f == 1 and w == 2
             and isinstance(v, bytes)]
    assert len(names) == 1
    features = [
        v for f, w, v in items if f == 2 and w == 2 and isinstance(v, bytes)
    ]
    keys = {
        v.decode() for f, w, v in items if f == 3 and w == 2
        and isinstance(v, bytes)
    }
    for feature in features:
        fields = _fields(feature)
        assert any(f == 4 for f, _, _ in fields)  # nonempty encoded geometry
    return names[0], features, keys


def test_source_tile_is_real_mvt_limited_version_scoped_and_cache_validated() -> None:
    project, foreign, version, other_version, run = _fixture()
    with Session(engine) as session:
        with session.begin():
            session.add_all(
                [
                    *(
                        SourceRoad(
                            dataset_version_id=version,
                            source_feature_id=f"source-{index}",
                            road_class="local",
                            attributes_json={"fixture_id": index},
                            geometry=_road(source=True),
                        )
                        for index in range(3)
                    ),
                    SourceRoad(
                        dataset_version_id=version,
                        source_feature_id="outside",
                        road_class="local",
                        geometry=_road(source=True, outside=True),
                    ),
                    SourceRoad(
                        dataset_version_id=other_version,
                        source_feature_id="foreign-version",
                        road_class="local",
                        geometry=_road(source=True),
                    ),
                ]
            )
            # Publish only after all source entities have been inserted:
            # ready dataset versions are DB-trigger protected and immutable.
            for selected_version in (version, other_version):
                stored = session.get(DatasetVersion, selected_version)
                assert stored is not None
                stored.status = "ready"

    params = {"dataset_version_id": str(version)}
    url = _endpoint(project, "source.roads")
    response = client.get(url, params={**params, "feature_limit": 1})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.mapbox-vector-tile"
    )
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    etag = response.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert response.headers["x-mvt-schema-version"] == "mvt-tile-v1"
    conditional = client.get(url, params={**params, "feature_limit": 1}, headers={
        "If-None-Match": etag,
    })
    assert conditional.status_code == 304
    assert conditional.content == b""
    assert conditional.headers["etag"] == etag
    assert conditional.headers["cache-control"] == response.headers["cache-control"]
    assert "content-type" not in conditional.headers
    for header in ("W/" + etag, '"other", ' + etag, "*"):
        assert client.get(
            url, params={**params, "feature_limit": 1},
            headers={"If-None-Match": header},
        ).status_code == 304
    assert client.get(
        url, params={**params, "feature_limit": 1},
        headers={"If-None-Match": '"invalid"'},
    ).status_code == 200
    assert response.headers["x-mvt-feature-limit"] == "1"
    assert response.headers["x-mvt-candidates"] == "2"
    assert response.headers["x-features-truncated"] == "true"
    layer, features, keys = _features(response.content)
    assert layer == "source.roads" and len(features) == 1
    assert {"feature_id", "road_class", "source_feature_id"} <= keys

    whole = client.get(url, params={**params, "feature_limit": 10})
    assert whole.status_code == 200, whole.text
    assert whole.headers["etag"] != etag  # Same owner; different rendering limit.
    assert whole.headers["x-features-truncated"] == "false"
    assert whole.headers["x-mvt-candidates"] == "3"
    layer, features, _ = _features(whole.content)
    assert layer == "source.roads" and len(features) == 3

    # An unlinked dataset version and foreign project cannot leak into tile.
    separate = client.get(
        url, params={"dataset_version_id": str(other_version)},
    )
    assert separate.status_code == 200
    assert separate.headers["etag"] != etag  # Owner identity is in validator.
    assert separate.headers["x-mvt-candidates"] == "1"
    assert client.get(
        _endpoint(foreign, "source.roads"), params=params,
    ).status_code == 404
    assert client.get(
        url, params={"dataset_version_id": str(uuid.uuid4())},
    ).status_code == 404
    assert client.get(
        url, params={**params, "run_id": str(run)},
    ).status_code == 422
    assert client.get(
        _endpoint(project, "generated.roads"), params=params,
    ).status_code == 422
    empty = client.get(
        _endpoint(project, "source.facilities"), params=params,
    )
    assert empty.status_code == 200
    assert empty.headers["x-mvt-candidates"] == "0"
    assert empty.content == b""
    assert empty.headers["etag"] != etag  # Different logical layer/empty body.
    assert client.get(
        url, params={**params, "feature_limit": 1, "run_id": str(run)},
        headers={"If-None-Match": "*"},
    ).status_code == 422
    assert client.get(
        _endpoint(foreign, "source.roads"), params=params,
        headers={"If-None-Match": "*"},
    ).status_code == 404


def test_run_tile_demand_readiness_and_linked_existing_facility_selection() -> None:
    project, foreign, version, other_version, run = _fixture()
    with Session(engine) as session:
        with session.begin():
            session.add_all(
                [
                    GeneratedRoad(
                        run_id=run,
                        geometry=_road(source=False),
                        attributes_json={"road_class": "local", "origin": "generated"},
                    ),
                    GeneratedBlock(
                        run_id=run,
                        geometry=_block(),
                        attributes_json={
                            "demography": {"population": 75, "jobs_estimate": 20},
                            "infrastructure": {"demand": 30},
                        },
                    ),
                    SourceFacility(
                        dataset_version_id=version, source_feature_id="linked",
                        facility_class="school", geometry=_facility(),
                    ),
                    SourceFacility(
                        dataset_version_id=other_version, source_feature_id="not-linked",
                        facility_class="school", geometry=_facility(),
                    ),
                ]
            )
    params = {"run_id": str(run)}
    roads = client.get(_endpoint(project, "generated.roads"), params=params)
    assert roads.status_code == 200, roads.text
    assert roads.headers["x-mvt-candidates"] == "1"
    name, feature_blobs, keys = _features(roads.content)
    assert name == "generated.roads" and len(feature_blobs) == 1
    assert "road_class" in keys and "feature_id" in keys

    existing = client.get(
        _endpoint(project, "run.existing_facilities"), params=params,
    )
    assert existing.status_code == 200, existing.text
    assert existing.headers["x-mvt-candidates"] == "1"
    _, item_blobs, fields = _features(existing.content)
    assert len(item_blobs) == 1
    assert {"origin", "dataset_version_id", "facility_class"} <= fields

    for layer in ("generated.demography", "generated.infrastructure_demand"):
        assert client.get(_endpoint(project, layer), params=params).status_code == 409

    with Session(engine) as session:
        with session.begin():
            stored = session.get(GenerationRun, run)
            assert stored is not None
            stored.metrics_json = {
                "demography": {"population": 75},
                "infrastructure": {"read_model_version": "1"},
            }
    demography = client.get(_endpoint(project, "generated.demography"), params=params)
    assert demography.status_code == 200, demography.text
    assert "population" in _features(demography.content)[2]
    demand = client.get(
        _endpoint(project, "generated.infrastructure_demand"), params=params,
    )
    assert demand.status_code == 200, demand.text
    assert "demand" in _features(demand.content)[2]
    assert client.get(
        _endpoint(foreign, "generated.roads"), params=params,
    ).status_code == 404
    assert client.get(
        _endpoint(project, "generated.roads"),
        params={"run_id": str(uuid.uuid4())},
    ).status_code == 404


@pytest.mark.parametrize(
    "layer,params,status",
    (
        ("source.roads", {}, 422),
        ("generated.roads", {}, 422),
        ("analysis.suitability", {}, 422),
        ("project.boundary", {}, 422),
        ("validation.violations", {}, 422),
        ("not-in-catalog", {}, 404),
        ("source.roads", {"z": -1}, 422),
    ),
)
def test_excluded_or_ownerless_tile_routes(
    layer: str, params: dict[str, object], status: int,
) -> None:
    project, _foreign, _version, _other, _run = _fixture()
    path = _endpoint(project, layer)
    if "z" in params:
        path = path.replace("/tiles/10/", f"/tiles/{params['z']}/")
    assert client.get(path).status_code == status


@pytest.mark.parametrize(
    "suffix,extra",
    (
        ("/tiles/17/0/0.mvt", {}),
        ("/tiles/-1/0/0.mvt", {}),
        ("/tiles/10/1024/340.mvt", {}),
        ("/tiles/10/511/1024.mvt", {}),
        (URL, {"feature_limit": 0}),
        (URL, {"feature_limit": 1001}),
        (URL, {"feature_limit": "nan"}),
    ),
)
def test_invalid_xyz_or_work_limit_rejected(suffix: str, extra: dict[str, object]) -> None:
    project, _foreign, version, _other, _run = _fixture()
    url = _endpoint(project, "source.roads").replace(URL, suffix)
    assert client.get(
        url, params={"dataset_version_id": str(version), **extra},
    ).status_code == 422



def test_active_and_succeeded_run_tile_caching_respects_linked_source_readiness() -> None:
    project, _foreign, version, _other_version, run = _fixture()
    with Session(engine) as session:
        with session.begin():
            session.add_all(
                [
                    GeneratedRoad(
                        run_id=run,
                        geometry=_road(source=False),
                        attributes_json={"road_class": "local", "origin": "generated"},
                    ),
                    SourceFacility(
                        dataset_version_id=version,
                        source_feature_id="existing",
                        facility_class="school",
                        geometry=_facility(),
                    ),
                ]
            )

    params = {"run_id": str(run)}
    generated_url = _endpoint(project, "generated.roads")
    existing_url = _endpoint(project, "run.existing_facilities")
    active = client.get(generated_url, params=params)
    assert active.status_code == 200, active.text
    assert active.headers["cache-control"] == "private, no-store"
    assert "etag" not in active.headers
    assert client.get(
        generated_url, params=params, headers={"If-None-Match": "*"},
    ).status_code == 200

    with Session(engine) as session:
        with session.begin():
            stored = session.get(GenerationRun, run)
            assert stored is not None
            stored.status = "succeeded"

    immutable_generated = client.get(generated_url, params=params)
    assert immutable_generated.status_code == 200, immutable_generated.text
    assert immutable_generated.headers["cache-control"] == (
        "private, max-age=31536000, immutable"
    )
    generated_tag = immutable_generated.headers["etag"]
    assert client.get(
        generated_url, params=params, headers={"If-None-Match": generated_tag},
    ).status_code == 304

    # The run result itself is fixed but existing-source rows remain writable
    # until *all* of its linked dataset versions have reached ready.
    unfinished_source = client.get(existing_url, params=params)
    assert unfinished_source.status_code == 200, unfinished_source.text
    assert unfinished_source.headers["cache-control"] == "private, no-store"
    assert "etag" not in unfinished_source.headers
    assert client.get(
        existing_url, params=params, headers={"If-None-Match": "*"},
    ).status_code == 200

    with Session(engine) as session:
        with session.begin():
            item = session.get(DatasetVersion, version)
            assert item is not None
            item.status = "ready"
    published_source = client.get(existing_url, params=params)
    assert published_source.status_code == 200, published_source.text
    assert published_source.headers["x-mvt-candidates"] == "1"
    assert published_source.headers["cache-control"] == (
        "private, max-age=31536000, immutable"
    )
    existing_tag = published_source.headers["etag"]
    assert existing_tag != generated_tag
    assert client.get(
        existing_url, params=params, headers={"If-None-Match": "W/" + existing_tag},
    ).status_code == 304
    assert client.get(
        generated_url, params=params, headers={"If-None-Match": existing_tag},
    ).status_code == 200


def test_nonready_source_tile_never_304_and_published_owner_inputs_are_guarded() -> None:
    project, foreign, version, _other_version, _run = _fixture()
    url = _endpoint(project, "source.roads")
    params = {"dataset_version_id": str(version)}
    pending = client.get(url, params=params, headers={"If-None-Match": "*"})
    assert pending.status_code == 200
    assert pending.content == b""
    assert pending.headers["cache-control"] == "private, no-store"
    assert "etag" not in pending.headers
    # A project without published version or successful run may still update
    # its working CRS; publication then seals that coordinate system.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE projects SET working_srid = 32637 WHERE id = :project_id"),
            {"project_id": project},
        )
    with Session(engine) as session:
        with session.begin():
            item = session.get(DatasetVersion, version)
            assert item is not None
            item.status = "ready"

    ready = client.get(url, params=params)
    assert ready.status_code == 200, ready.text
    etag = ready.headers["etag"]
    assert client.get(
        url, params=params, headers={"If-None-Match": etag},
    ).status_code == 304

    # SQL migration guards also protect writes bypassing the ORM.
    for statement, parameters, message in (
        (
            "UPDATE dataset_versions SET status = 'processing' WHERE id = :version_id",
            {"version_id": version},
            "ready dataset version status is terminal",
        ),
        (
            "UPDATE projects SET working_srid = 3857 WHERE id = :project_id",
            {"project_id": project},
            "project working_srid is immutable",
        ),
        (
            "UPDATE datasets SET project_id = :foreign_id WHERE id = "
            "(SELECT dataset_id FROM dataset_versions WHERE id = :version_id)",
            {"foreign_id": foreign, "version_id": version},
            "published dataset versions cannot be moved",
        ),
    ):
        with pytest.raises(DBAPIError, match=message):
            with engine.begin() as conn:
                conn.execute(text(statement), parameters)
    assert client.get(
        url, params=params, headers={"If-None-Match": etag},
    ).status_code == 304



def test_source_row_write_blocks_concurrent_ready_transition() -> None:
    project, _foreign, version, _other, _run = _fixture()
    with engine.connect() as first:
        with first.begin():
            first.execute(
                text(
                    "INSERT INTO source_facilities "
                    "(id, dataset_version_id, source_feature_id, facility_class, "
                    "attributes_json, geometry) "
                    "VALUES (:id, :version, 'pending', 'school', '{}'::jsonb, "
                    "ST_SetSRID(ST_MakePoint(0, 0), 3857))"
                ),
                {"id": uuid.uuid4(), "version": version},
            )
            # The inserted row's BEFORE trigger holds a SHARE lock on the
            # version until commit. Ready publication must wait for that
            # write, so no tile is ever declared immutable prematurely.
            with engine.connect() as second:
                with pytest.raises(DBAPIError, match="lock timeout"):
                    with second.begin():
                        second.execute(text("SET LOCAL lock_timeout = '200ms'"))
                        second.execute(
                            text(
                                "UPDATE dataset_versions SET status = 'ready' "
                                "WHERE id = :version"
                            ),
                            {"version": version},
                        )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dataset_versions SET status = 'ready' "
                "WHERE id = :version"
            ),
            {"version": version},
        )
    assert client.get(
        _endpoint(project, "source.facilities"),
        params={"dataset_version_id": str(version)},
    ).headers["etag"]
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO source_facilities "
                    "(id, dataset_version_id, source_feature_id, facility_class, "
                    "attributes_json, geometry) "
                    "VALUES (:id, :version, 'late', 'school', '{}'::jsonb, "
                    "ST_SetSRID(ST_MakePoint(0, 0), 3857))"
                ),
                {"id": uuid.uuid4(), "version": version},
            )


def test_generated_row_write_blocks_concurrent_success_transition() -> None:
    project, _foreign, _version, _other, run = _fixture()
    with engine.connect() as first:
        with first.begin():
            first.execute(
                text(
                    "INSERT INTO generated_roads (id, run_id, attributes_json, geometry) "
                    "VALUES (:id, :run, '{}'::jsonb, "
                    "ST_GeomFromText('LINESTRING(0 0, 1 1)', 3857))"
                ),
                {"id": uuid.uuid4(), "run": run},
            )
            with engine.connect() as second:
                with pytest.raises(DBAPIError, match="lock timeout"):
                    with second.begin():
                        second.execute(text("SET LOCAL lock_timeout = '200ms'"))
                        second.execute(
                            text(
                                "UPDATE generation_runs SET status = 'succeeded' "
                                "WHERE id = :run"
                            ),
                            {"run": run},
                        )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE generation_runs SET status = 'succeeded' "
                "WHERE id = :run"
            ),
            {"run": run},
        )
    response = client.get(
        _endpoint(project, "generated.roads"), params={"run_id": str(run)},
    )
    assert response.status_code == 200, response.text
    assert response.headers["etag"]
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO generated_roads (id, run_id, attributes_json, geometry) "
                    "VALUES (:id, :run, '{}'::jsonb, "
                    "ST_GeomFromText('LINESTRING(0 0, 1 1)', 3857))"
                ),
                {"id": uuid.uuid4(), "run": run},
            )
