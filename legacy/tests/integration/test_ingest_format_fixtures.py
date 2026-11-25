import io
import json
import uuid
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from alembic import command
from alembic.config import Config
from rasterio.transform import from_origin
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from backend.app.adapters import LocalArtifactStore
from backend.app.application.ingest import (
    INGEST_JOB_TYPE,
    IngestJobRunStatus,
    IngestJobService,
    ingest_idempotency_key,
)
from backend.app.db.ingest_job_repository import SqlAlchemyIngestJobRepository
from backend.app.db.session import engine
from backend.app.db.source_layer_writer import SqlAlchemySourceLayerBatchWriter
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.job import Job
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceRoad
from backend.app.services.ingest_pipeline import DatasetIngestPipeline
from backend.app.services.osm_pbf_reader import (
    OsmFeatureCategory,
    OsmPbfReader,
    OsmPbfReadError,
    OsmPbfReadLimits,
)
from core.urban_generator.domain import ArtifactRef

FIXTURE_ROOT = Path("tests/fixtures/ingest")
FIXTURE_MANIFEST = FIXTURE_ROOT / "manifest.json"
ROADS_GEOJSON = FIXTURE_ROOT / "roads.geojson"
WORKING_SRID = 3857
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _migrate_to_head() -> None:
    command.upgrade(Config("alembic.ini"), "head")


def _truncate() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None):
    _truncate()
    yield
    _truncate()


def _manifest() -> dict[str, object]:
    raw = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    fixtures = raw["fixtures"]
    assert isinstance(fixtures, dict)
    return fixtures


def _fixture_spec(format_name: str) -> dict[str, object]:
    value = _manifest()[format_name]
    assert isinstance(value, dict)
    return value


def _content_type(source_format: str) -> str:
    return {
        "geojson": "application/geo+json",
        "gpkg": "application/geopackage+sqlite3",
        "shapefile_zip": "application/zip",
        "geotiff": "image/tiff",
    }[source_format]


def _materialize_happy_fixture(
    tmp_path: Path,
    *,
    source_format: str,
) -> tuple[Path, str | None]:
    if source_format == "geojson":
        return ROADS_GEOJSON, None

    if source_format in {"gpkg", "shapefile_zip"}:
        frame = gpd.read_file(ROADS_GEOJSON)
        if source_format == "gpkg":
            path = tmp_path / "roads.gpkg"
            frame.to_file(path, layer="roads", driver="GPKG")
            return path, "roads"

        shapefile_dir = tmp_path / "shapefile"
        shapefile_dir.mkdir()
        shapefile_path = shapefile_dir / "roads.shp"
        frame.to_file(shapefile_path, driver="ESRI Shapefile")
        archive_path = tmp_path / "roads.zip"
        with zipfile.ZipFile(
            archive_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for component in sorted(shapefile_dir.iterdir()):
                archive.write(component, arcname=component.name)
        return archive_path, "roads"

    if source_format == "geotiff":
        path = tmp_path / "terrain.tif"
        data = np.arange(100, dtype=np.uint16).reshape((10, 10))
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=10,
            height=10,
            count=1,
            dtype="uint16",
            crs="EPSG:4326",
            transform=from_origin(0.0, 0.01, 0.001, 0.001),
            nodata=65535,
        ) as target:
            target.write(data, 1)
        return path, None

    raise AssertionError(f"unsupported fixture format: {source_format}")


def _materialize_invalid_fixture(tmp_path: Path, *, source_format: str) -> Path:
    if source_format == "geojson":
        path = tmp_path / "invalid.geojson"
        path.write_text('{"type":"FeatureCollection","features":[', encoding="utf-8")
        return path
    if source_format == "gpkg":
        path = tmp_path / "invalid.gpkg"
        path.write_bytes(b"not-a-geopackage\n")
        return path
    if source_format == "shapefile_zip":
        path = tmp_path / "invalid.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("README.txt", "missing shapefile components")
        return path
    if source_format == "geotiff":
        path = tmp_path / "invalid.tif"
        path.write_bytes(b"not-a-geotiff\n")
        return path
    raise AssertionError(f"unsupported fixture format: {source_format}")


def _create_ingest_state(
    store: LocalArtifactStore,
    *,
    fixture: Path,
    source_format: str,
    dataset_kind: str,
    layer: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    temporary_ref = ArtifactRef(f"uploads/{uuid.uuid4().hex}/{fixture.name}")
    store.put(
        temporary_ref,
        io.BytesIO(fixture.read_bytes()),
        content_type=_content_type(source_format),
    )
    source_stat = store.promote(temporary_ref)

    source_metadata: dict[str, object] = {
        "artifact_key": source_stat.ref.key,
        "format": source_format,
    }
    if layer is not None:
        source_metadata["layer"] = layer

    with SessionFactory() as session:
        project = Project(
            name=f"S03-T15 {source_format}",
            working_srid=WORKING_SRID,
            boundary_metadata={},
        )
        session.add(project)
        session.flush()
        dataset = Dataset(project_id=project.id, kind=dataset_kind)
        session.add(dataset)
        session.flush()
        version = DatasetVersion(
            dataset_id=dataset.id,
            version=1,
            checksum_sha256=source_stat.checksum.removeprefix("sha256:"),
            status="uploaded",
            source_metadata=source_metadata,
        )
        session.add(version)
        session.flush()
        session.add(
            Artifact(
                uri=f"artifact://{source_stat.ref.key}",
                checksum=source_stat.checksum,
                size_bytes=source_stat.size_bytes,
                content_type=source_stat.content_type,
                state=ArtifactLifecycleState.READY.value,
            )
        )
        job = Job(
            project_id=project.id,
            job_type=INGEST_JOB_TYPE,
            idempotency_key=ingest_idempotency_key(version.id),
            status="queued",
            attempt_count=0,
            max_attempts=2,
        )
        session.add(job)
        session.commit()
        return job.id, version.id


def _service(store: LocalArtifactStore) -> IngestJobService:
    return IngestJobService(
        repository=SqlAlchemyIngestJobRepository(session_factory=SessionFactory),
        pipeline=DatasetIngestPipeline(
            store=store,
            vector_writer=SqlAlchemySourceLayerBatchWriter(
                session_factory=SessionFactory
            ),
        ),
    )


@pytest.mark.parametrize("source_format", ["geojson", "gpkg", "shapefile_zip"])
def test_vector_happy_fixtures_reach_ready_canonical_rows(
    tmp_path: Path,
    source_format: str,
) -> None:
    fixture, layer = _materialize_happy_fixture(
        tmp_path,
        source_format=source_format,
    )
    store = LocalArtifactStore(tmp_path / "storage")
    job_id, version_id = _create_ingest_state(
        store,
        fixture=fixture,
        source_format=source_format,
        dataset_kind="roads",
        layer=layer,
    )

    result = _service(store).run(job_id=job_id, dataset_version_id=version_id)

    assert result.status is IngestJobRunStatus.SUCCEEDED
    assert result.details is not None
    assert result.details["inserted_rows"] == 2
    with SessionFactory() as session:
        version = session.get(DatasetVersion, version_id)
        count = session.scalar(
            select(func.count())
            .select_from(SourceRoad)
            .where(SourceRoad.dataset_version_id == version_id)
        )
        srids = session.scalars(
            select(func.ST_SRID(SourceRoad.geometry))
            .where(SourceRoad.dataset_version_id == version_id)
        ).all()
    assert version is not None and version.status == "ready"
    assert count == 2
    assert srids == [WORKING_SRID, WORKING_SRID]


@pytest.mark.parametrize("source_format", ["geojson", "gpkg", "shapefile_zip"])
def test_vector_invalid_fixtures_fail_without_canonical_rows(
    tmp_path: Path,
    source_format: str,
) -> None:
    fixture = _materialize_invalid_fixture(
        tmp_path,
        source_format=source_format,
    )
    store = LocalArtifactStore(tmp_path / "storage")
    job_id, version_id = _create_ingest_state(
        store,
        fixture=fixture,
        source_format=source_format,
        dataset_kind="roads",
        layer="roads" if source_format in {"gpkg", "shapefile_zip"} else None,
    )

    result = _service(store).run(job_id=job_id, dataset_version_id=version_id)

    assert result.status is IngestJobRunStatus.FAILED
    assert result.retryable is False
    with SessionFactory() as session:
        version = session.get(DatasetVersion, version_id)
        job = session.get(Job, job_id)
        count = session.scalar(
            select(func.count())
            .select_from(SourceRoad)
            .where(SourceRoad.dataset_version_id == version_id)
        )
    assert version is not None and version.status == "failed"
    assert job is not None and job.error_class == "data"
    assert count == 0


def test_geotiff_happy_fixture_normalizes_to_ready_artifact(tmp_path: Path) -> None:
    fixture, _layer = _materialize_happy_fixture(
        tmp_path,
        source_format="geotiff",
    )
    store = LocalArtifactStore(tmp_path / "storage")
    job_id, version_id = _create_ingest_state(
        store,
        fixture=fixture,
        source_format="geotiff",
        dataset_kind="dem",
    )

    result = _service(store).run(job_id=job_id, dataset_version_id=version_id)

    assert result.status is IngestJobRunStatus.SUCCEEDED
    assert result.details is not None
    assert result.details["kind"] == "raster"
    normalized_key = result.details["normalized_artifact_key"]
    assert isinstance(normalized_key, str)
    assert store.stat(ArtifactRef(normalized_key).as_ready()).size_bytes > 0
    with SessionFactory() as session:
        version = session.get(DatasetVersion, version_id)
    assert version is not None and version.status == "ready"


def test_geotiff_invalid_fixture_is_permanent_data_failure(tmp_path: Path) -> None:
    fixture = _materialize_invalid_fixture(
        tmp_path,
        source_format="geotiff",
    )
    store = LocalArtifactStore(tmp_path / "storage")
    job_id, version_id = _create_ingest_state(
        store,
        fixture=fixture,
        source_format="geotiff",
        dataset_kind="dem",
    )

    result = _service(store).run(job_id=job_id, dataset_version_id=version_id)

    assert result.status is IngestJobRunStatus.FAILED
    assert result.retryable is False
    with SessionFactory() as session:
        version = session.get(DatasetVersion, version_id)
        job = session.get(Job, job_id)
    assert version is not None and version.status == "failed"
    assert job is not None and job.error_class == "data"


def test_osm_pbf_happy_fixture_exposes_all_expected_source_families() -> None:
    fixture = Path(str(_fixture_spec("osm_pbf")["happy"]))
    reader = OsmPbfReader(
        limits=OsmPbfReadLimits(batch_size=2, max_features_per_layer=20)
    )

    categories = {batch.category for batch in reader.iter_batches(fixture)}

    assert categories == set(OsmFeatureCategory)


def test_osm_pbf_invalid_fixture_is_rejected_by_real_gdal_boundary(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "invalid.pbf"
    invalid.write_bytes(b"not-an-osm-pbf\n")
    reader = OsmPbfReader(
        limits=OsmPbfReadLimits(batch_size=2, max_features_per_layer=20)
    )

    with pytest.raises(OsmPbfReadError):
        tuple(reader.iter_batches(invalid))


def test_fixture_manifest_documents_full_sprint_matrix() -> None:
    fixtures = _manifest()

    assert set(fixtures) == {
        "geojson",
        "gpkg",
        "shapefile_zip",
        "geotiff",
        "osm_pbf",
    }
    assert ROADS_GEOJSON.is_file()
    assert ROADS_GEOJSON.stat().st_size > 0
    pbf = Path(str(_fixture_spec("osm_pbf")["happy"]))
    assert pbf.is_file()
    assert pbf.stat().st_size > 0
