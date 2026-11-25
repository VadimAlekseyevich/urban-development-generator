"""Real GeoTIFF bytes and PostGIS ownership -> canonical slope evaluation."""

import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from rasterio.transform import from_origin
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.adapters.dem_source_reader import DEMSourceReadError, SqlAlchemyDEMSourceReader
from backend.app.adapters.local_artifact_store import LocalArtifactStore
from backend.app.adapters.pipeline_context import SqlAlchemyPipelineContextAdapter
from backend.app.db.session import engine
from backend.app.models.artifact import Artifact
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.domain import (
    ArtifactRef,
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
from core.urban_generator.suitability import DEMSlopeFactor, DEMSlopeWindow, SuitabilityGridSpec

SRID = 32637


def _session() -> Session:
    return Session(engine, expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture
def dem(tmp_path: Path):
    path = tmp_path / "input.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=4, height=4, count=1,
        crs=f"EPSG:{SRID}", transform=from_origin(500000, 6000040, 10, 10),
        dtype="float32", nodata=-9999,
    ) as dataset:
        dataset.write(np.tile(np.arange(4, dtype=np.float32), (4, 1)), 1)
    store = LocalArtifactStore(tmp_path / "store")
    with _session() as session:
        project = Project(
            name="DEM source fixture", working_srid=SRID, boundary_metadata={},
            boundary=WKTElement(
                "MULTIPOLYGON(((500000 6000000,500040 6000000,"
                "500040 6000040,500000 6000040,500000 6000000)))", srid=SRID,
            ),
        )
        session.add(project)
        session.flush()
        dataset = Dataset(project_id=project.id, kind="dem")
        session.add(dataset)
        session.flush()
        version = DatasetVersion(
            dataset_id=dataset.id, version=1, status="processing",
            checksum_sha256="b" * 64, source_metadata={"format": "geotiff"},
        )
        session.add(version)
        session.flush()
        ref = ArtifactRef(key=f"datasets/{version.id}/normalized/raster.tif")
        with path.open("rb") as stream:
            store.put(ref, stream, content_type="image/tiff")
        stat = store.promote(ref)
        session.add(Artifact(
            uri=f"artifact://{ref.key}", checksum=stat.checksum,
            size_bytes=stat.size_bytes, content_type=stat.content_type,
            state="referenced", owner_type="dataset_version", owner_id=version.id,
        ))
        session.flush()
        version.status = "ready"
        session.flush()
        run = GenerationRun(
            project_id=project.id, mode="FROM_SCRATCH", status="queued",
            seed=2026, working_srid=SRID, config_json={}, config_schema_version="fixture-v1",
        )
        run.dataset_versions.append(version)
        session.add(run)
        session.commit()
        snapshot = TerritorySnapshot(
            snapshot_id=run.id, project=ProjectRef(project_id=project.id),
            settings=ProjectSettings(working_srid=SRID),
            boundary=SnapshotLayerRef(
                kind=SnapshotLayerKind.BOUNDARY,
                source_ref=SqlAlchemyPipelineContextAdapter._boundary_source_ref(project),
            ),
            dem=(SnapshotLayerRef(
                kind=SnapshotLayerKind.DEM,
                source_ref=f"dataset-version:{version.id}:v1",
            ),),
        )
    grid = SuitabilityGridSpec(
        working_srid=SRID, bounds=(500000, 6000000, 500040, 6000040), width=4, height=4,
    )
    return store, snapshot, grid, ref.as_ready()


def test_persisted_dem_feeds_real_slope_factor_and_masks_outside_coverage(dem) -> None:
    store, snapshot, grid, _ = dem
    reader = SqlAlchemyDEMSourceReader(artifact_store=store, session_factory=_session)
    context = RunContext(
        run_id=snapshot.snapshot_id, mode=RunMode.FROM_SCRATCH, seed=2026, working_srid=SRID,
        config_refs=(ConfigRef(name="generation", ref="fixture"),),
        correlation=CorrelationMetadata(correlation_id="dem-source-test"),
    )
    with reader.open(snapshot=snapshot, grid=grid) as source:
        assert source.read_window(window=DEMSlopeWindow(0, 1, 2, 2)) == ((1., 2.), (1., 2.))
        result = DEMSlopeFactor(source=source, tile_size=2).evaluate(
            grid=grid, snapshot=snapshot, context=context,
        )
        assert np.all(result.valid_mask)
        assert np.allclose(result.values, np.degrees(np.arctan(0.1)))
        with pytest.raises(DEMSourceReadError, match="outside grid"):
            source.read_window(window=DEMSlopeWindow(3, 3, 2, 2))
    expanded = SuitabilityGridSpec(
        working_srid=SRID, bounds=(499990, 6000000, 500040, 6000040), width=5, height=4,
    )
    with reader.open(snapshot=snapshot, grid=expanded) as source:
        assert source.read_window(window=DEMSlopeWindow(0, 0, 1, 2)) == ((None, 0.),)


def test_dem_rejects_unlinked_version_and_size_budget(dem) -> None:
    store, snapshot, grid, _ = dem
    reader = SqlAlchemyDEMSourceReader(artifact_store=store, session_factory=_session)
    forged = replace(snapshot, dem=(SnapshotLayerRef(
        kind=SnapshotLayerKind.DEM, source_ref=f"dataset-version:{uuid.uuid4()}:v1",
    ),))
    with pytest.raises(DEMSourceReadError, match="exact ready run-linked"):
        with reader.open(snapshot=forged, grid=grid):
            pytest.fail("unauthorized source")
    small = SqlAlchemyDEMSourceReader(
        artifact_store=store, session_factory=_session, max_bytes=1,
    )
    with pytest.raises(DEMSourceReadError, match="scratch budget"):
        with small.open(snapshot=snapshot, grid=grid):
            pytest.fail("oversized source")


def test_dem_rejects_missing_artifact(dem) -> None:
    store, snapshot, grid, ref = dem
    store.delete(ref)
    with pytest.raises(DEMSourceReadError, match="unable to read"):
        with SqlAlchemyDEMSourceReader(
            artifact_store=store, session_factory=_session,
        ).open(snapshot=snapshot, grid=grid):
            pytest.fail("missing artifact")


def test_dem_rejects_same_size_corrupted_payload(dem, tmp_path: Path) -> None:
    store, snapshot, grid, ref = dem
    payload = tmp_path / "store" / "ready" / ref.key
    with payload.open("r+b") as stream:
        original = stream.read(1)
        assert original
        stream.seek(0)
        stream.write(bytes([original[0] ^ 0xFF]))
    reader = SqlAlchemyDEMSourceReader(artifact_store=store, session_factory=_session)
    with pytest.raises(DEMSourceReadError, match="checksum"):
        with reader.open(snapshot=snapshot, grid=grid):
            pytest.fail("corrupted artifact")
