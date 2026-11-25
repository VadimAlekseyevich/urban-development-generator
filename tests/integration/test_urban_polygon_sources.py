"""Real PostGIS polygons -> core landuse factor and building spacing index."""

import uuid
from dataclasses import replace

import numpy as np
import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session
from shapely.geometry import MultiPolygon, box

from backend.app.adapters.pipeline_context import SqlAlchemyPipelineContextAdapter
from backend.app.adapters.urban_polygon_sources import (
    SqlAlchemyUrbanPolygonSourceReader,
    UrbanPolygonSourceError,
)
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceBuilding, SourceLanduse
from core.urban_generator.buildings import BuildingSpacingIndex, BuildingSpacingSubject
from core.urban_generator.domain import (
    ConfigRef,
    CorrelationMetadata,
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.suitability import (
    LanduseClassWeight,
    LanduseClassWeights,
    LanduseFactor,
    LanduseWindow,
    SuitabilityGridSpec,
)

SRID = 32637


def _session() -> Session:
    return Session(engine, expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _polygon(x0, y0, x1, y1):
    return WKTElement(MultiPolygon([box(x0, y0, x1, y1)]).wkt, srid=SRID)


@pytest.fixture
def scenario():
    with _session() as session:
        project = Project(
            name="Polygon adapter fixture", working_srid=SRID,
            boundary_metadata={}, boundary=_polygon(0, 0, 40, 40),
        )
        session.add(project)
        session.flush()
        versions = []
        for kind in ("landuse", "buildings"):
            dataset = Dataset(project_id=project.id, kind=kind)
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id, version=1, status="processing",
                checksum_sha256="b" * 64, source_metadata={},
            )
            session.add(version)
            session.flush()
            versions.append(version)
        land, buildings = versions
        for i, (label, bounds) in enumerate((
            ("residential", (0, 0, 30, 40)),
            ("forest", (20, 0, 40, 40)),
            ("residential", (0, 0, 30, 40)),
            ("forest", (100, 100, 110, 110)),
        )):
            session.add(SourceLanduse(
                dataset_version_id=land.id, source_feature_id=str(i),
                landuse_class=label, geometry=_polygon(*bounds), attributes_json={},
            ))
        session.add(SourceBuilding(
            dataset_version_id=buildings.id, source_feature_id="building-a",
            building_class="residential", geometry=_polygon(5, 5, 15, 15),
            attributes_json={},
        ))
        session.flush()
        for version in versions:
            version.status = "ready"
        session.flush()
        run = GenerationRun(
            project_id=project.id, mode="EXPANSION", status="queued", seed=2026,
            working_srid=SRID, config_json={}, config_schema_version="fixture",
        )
        run.dataset_versions.extend(versions)
        session.add(run)
        session.commit()
        snapshot = TerritorySnapshot(
            snapshot_id=run.id, project=ProjectRef(project_id=project.id),
            settings=ProjectSettings(working_srid=SRID),
            boundary=SnapshotLayerRef(
                kind=SnapshotLayerKind.BOUNDARY,
                source_ref=SqlAlchemyPipelineContextAdapter._boundary_source_ref(project),
            ),
            landuse=(SnapshotLayerRef(
                kind=SnapshotLayerKind.LANDUSE, source_ref=f"dataset-version:{land.id}:v1",
            ),),
            buildings=(SnapshotLayerRef(
                kind=SnapshotLayerKind.BUILDINGS,
                source_ref=f"dataset-version:{buildings.id}:v1",
            ),),
        )
    return snapshot, SuitabilityGridSpec(
        working_srid=SRID, bounds=(0, 0, 40, 40), width=4, height=4,
    )


def test_landuse_conflicts_are_invalid_and_tiles_match(scenario) -> None:
    snapshot, grid = scenario
    reader = SqlAlchemyUrbanPolygonSourceReader(session_factory=_session)
    source = reader.read_landuse(snapshot=snapshot, grid=grid)
    expected = ("residential", "residential", None, "forest")
    assert source.read_window(window=LanduseWindow(0, 0, 4, 4)) == (expected,) * 4
    assert source.read_window(window=LanduseWindow(1, 1, 2, 3)) == (expected[1:],) * 2
    context = RunContext(
        run_id=snapshot.snapshot_id, mode=RunMode.EXPANSION, seed=2026, working_srid=SRID,
        config_refs=(ConfigRef(name="generation", ref="fixture"),),
        correlation=CorrelationMetadata(correlation_id="polygon-source"),
    )
    result = LanduseFactor(
        source=source, tile_size=2, weights=LanduseClassWeights(
            version="v1", classes=(
                LanduseClassWeight("residential", 0.8),
                LanduseClassWeight("forest", 0.1),
            ),
        ),
    ).evaluate(grid=grid, snapshot=snapshot, context=context)
    assert np.allclose(result.values[0], [0.8, 0.8, 0, 0.1])
    assert result.valid_mask[0].tolist() == [True, True, False, True]


def test_fixed_buildings_feed_spacing_and_remain_unchanged(scenario) -> None:
    snapshot, _ = scenario
    reader = SqlAlchemyUrbanPolygonSourceReader(session_factory=_session)
    footprints = reader.read_buildings(snapshot=snapshot)
    assert len(footprints) == 1
    assert footprints[0].geometry.area == 100
    index = BuildingSpacingIndex(footprints=footprints, working_srid=SRID)
    assert index.check(BuildingSpacingSubject(box(10, 10, 20, 20), SRID)) is not None
    assert index.check(BuildingSpacingSubject(box(20, 20, 25, 25), SRID)) is None
    assert reader.read_buildings(snapshot=snapshot) == footprints


def test_reject_forged_omitted_and_oversized_source(scenario) -> None:
    snapshot, grid = scenario
    reader = SqlAlchemyUrbanPolygonSourceReader(session_factory=_session)
    forged = replace(snapshot, landuse=(SnapshotLayerRef(
        kind=SnapshotLayerKind.LANDUSE, source_ref=f"dataset-version:{uuid.uuid4()}:v1",
    ),))
    with pytest.raises(UrbanPolygonSourceError, match="ready run-linked"):
        reader.read_landuse(snapshot=forged, grid=grid)
    with pytest.raises(UrbanPolygonSourceError, match="ready run-linked"):
        reader.read_buildings(snapshot=replace(snapshot, buildings=()))
    with pytest.raises(UrbanPolygonSourceError, match="feature budget"):
        SqlAlchemyUrbanPolygonSourceReader(
            session_factory=_session, max_features=1,
        ).read_landuse(snapshot=snapshot, grid=grid)
