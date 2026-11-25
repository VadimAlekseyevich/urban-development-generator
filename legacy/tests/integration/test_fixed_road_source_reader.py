"""Real PostGIS road rows -> immutable typed canonical fixed-road inputs."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.adapters.fixed_road_source_reader import (
    FixedRoadSourceReadError,
    SqlAlchemyFixedRoadSourceReader,
)
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceRoad
from core.urban_generator.domain import (
    ProjectRef,
    ProjectSettings,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)

WORKING_SRID = 32637


def _session() -> Session:
    return Session(engine, expire_on_commit=False)


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


def _project(session: Session) -> Project:
    project = Project(
        name="Fixed road source reader",
        working_srid=WORKING_SRID,
        boundary_metadata={},
        boundary=WKTElement(
            "MULTIPOLYGON(((0 0, 100 0, 100 100, 0 100, 0 0)))",
            srid=WORKING_SRID,
        ),
    )
    session.add(project)
    session.flush()
    return project


def _version(
    session: Session,
    project: Project,
    *,
    kind: str = "osm",
) -> DatasetVersion:
    dataset = Dataset(project_id=project.id, kind=kind)
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        status="processing",
        checksum_sha256="a" * 64,
        source_metadata={"source": "real-postgis-roads"},
    )
    session.add(version)
    session.flush()
    return version


def _road(
    session: Session,
    version: DatasetVersion,
    *,
    feature_id: str,
    geometry: str = "MULTILINESTRING((10 10, 20 20))",
    road_class: str = "local",
    layer: int = 0,
    bridge: bool = False,
) -> None:
    session.add(
        SourceRoad(
            dataset_version_id=version.id,
            source_feature_id=feature_id,
            road_class=road_class,
            geometry=WKTElement(geometry, srid=WORKING_SRID),
            layer=layer,
            bridge=bridge,
        )
    )


def _run(session: Session, project: Project, *versions: DatasetVersion) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        mode="EXPANSION",
        status="queued",
        seed=2026,
        working_srid=WORKING_SRID,
        config_json={},
        config_schema_version="test-v1",
    )
    run.dataset_versions.extend(versions)
    session.add(run)
    session.commit()
    return run


def _snapshot(
    project: Project,
    run: GenerationRun,
    *versions: DatasetVersion,
) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=run.id,
        project=ProjectRef(project_id=project.id),
        settings=ProjectSettings(working_srid=WORKING_SRID),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref=f"test-boundary:{project.id}",
        ),
        roads=tuple(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.ROADS,
                source_ref=f"dataset-version:{version.id}:v{version.version}",
            )
            for version in versions
        ),
    )


def test_reader_loads_only_boundary_intersecting_roads_with_fixed_semantics() -> None:
    with _session() as session:
        project = _project(session)
        osm = _version(session, project)
        _road(session, osm, feature_id="b", layer=1, bridge=True, road_class="primary")
        _road(session, osm, feature_id="a", road_class="local")
        _road(
            session, osm, feature_id="outside",
            geometry="MULTILINESTRING((200 200, 300 300))",
        )
        session.flush()
        osm.status = "ready"
        session.flush()
        run = _run(session, project, osm)
        snapshot = _snapshot(project, run, osm)

    roads = SqlAlchemyFixedRoadSourceReader(session_factory=_session).read(
        snapshot=snapshot
    )

    assert tuple(item.road.road_id for item in roads) == (
        f"fixed:{osm.id}:a",
        f"fixed:{osm.id}:b",
    )
    assert roads[0].existing_class == "local"
    assert roads[1].existing_class == "primary"
    assert roads[1].road.layer == 1
    assert roads[1].road.bridge is True
    assert roads[1].road.tunnel is False
    assert roads[0].road.geometry.length > 0


def test_reader_rejects_unlinked_versions_and_oversized_input() -> None:
    with _session() as session:
        project = _project(session)
        first = _version(session, project)
        second = _version(session, project)
        _road(session, first, feature_id="a")
        _road(session, first, feature_id="b")
        _road(session, second, feature_id="c")
        session.flush()
        first.status = "ready"
        second.status = "ready"
        session.flush()
        run = _run(session, project, first)
        own_snapshot = _snapshot(project, run, first)
        unlinked_snapshot = _snapshot(project, run, second)

    with pytest.raises(FixedRoadSourceReadError, match="configured limit"):
        SqlAlchemyFixedRoadSourceReader(
            session_factory=_session, max_roads=1
        ).read(snapshot=own_snapshot)

    with pytest.raises(FixedRoadSourceReadError, match="not linked to run"):
        SqlAlchemyFixedRoadSourceReader(
            session_factory=_session
        ).read(snapshot=unlinked_snapshot)


def test_reader_rejects_processing_versions_and_bad_refs() -> None:
    with _session() as session:
        project = _project(session)
        processing = _version(session, project, kind="roads")
        _road(session, processing, feature_id="pending")
        session.flush()
        run = _run(session, project, processing)
        snapshot = _snapshot(project, run, processing)

    reader = SqlAlchemyFixedRoadSourceReader(session_factory=_session)
    with pytest.raises(FixedRoadSourceReadError, match="not ready"):
        reader.read(snapshot=snapshot)

    bad_snapshot = TerritorySnapshot(
        snapshot_id=snapshot.snapshot_id,
        project=snapshot.project,
        settings=snapshot.settings,
        boundary=snapshot.boundary,
        roads=(SnapshotLayerRef(
            kind=SnapshotLayerKind.ROADS,
            source_ref="dataset-version:not-a-uuid:v1",
        ),),
    )
    with pytest.raises(FixedRoadSourceReadError, match="invalid dataset-version"):
        reader.read(snapshot=bad_snapshot)

    assert reader.read(
        snapshot=TerritorySnapshot(
            snapshot_id=snapshot.snapshot_id,
            project=snapshot.project,
            settings=snapshot.settings,
            boundary=snapshot.boundary,
        )
    ) == ()
