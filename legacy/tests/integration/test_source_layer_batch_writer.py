import uuid

import geopandas as gpd
import pytest
from alembic import command
from alembic.config import Config
from shapely.geometry import LineString, Polygon
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.db.source_layer_writer import (
    CanonicalSourceLayer,
    PostLoadAnalyzePolicy,
    SourceLayerImmutableError,
    SourceLayerPersistenceError,
    SqlAlchemySourceLayerBatchWriter,
)
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project
from backend.app.services.vector_normalization import (
    NormalizedVectorBatch,
    VectorBatchDiagnostics,
)

WORKING_SRID = 32637


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


def _session_factory() -> Session:
    return Session(engine, expire_on_commit=False)


def _create_dataset_version(*, status: str = "processing") -> tuple[uuid.UUID, uuid.UUID]:
    with _session_factory() as session:
        with session.begin():
            project = Project(
                name="Vector persistence",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            dataset = Dataset(project_id=project.id, kind="roads")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status=status,
                source_metadata={},
            )
            session.add(version)
            session.flush()
            return project.id, version.id


def _batch(
    *,
    layer_name: str,
    start_feature: int,
    feature_ids: list[str],
    geometries: list[object],
    road_classes: list[str],
    crs: str = f"EPSG:{WORKING_SRID}",
    working_srid: int = WORKING_SRID,
) -> NormalizedVectorBatch:
    frame = gpd.GeoDataFrame(
        {
            "source_feature_id": feature_ids,
            "road_class": road_classes,
            "name": [f"road-{feature_id}" for feature_id in feature_ids],
            "source_tag": [f"tag-{feature_id}" for feature_id in feature_ids],
        },
        geometry=geometries,
        crs=crs,
    )
    return NormalizedVectorBatch(
        layer_name=layer_name,
        start_feature=start_feature,
        source_crs=crs,
        working_srid=working_srid,
        frame=frame,
        diagnostics=VectorBatchDiagnostics(
            input_features=len(frame),
            output_features=len(frame),
            repaired_features=0,
            dropped_empty_features=0,
            dropped_type_features=0,
            filtered_collection_features=0,
        ),
    )


def test_writer_bulk_persists_batches_with_working_srid_and_attributes() -> None:
    _project_id, version_id = _create_dataset_version()
    writer = SqlAlchemySourceLayerBatchWriter(
        session_factory=_session_factory,
        max_insert_rows=1,
        analyze_policy=PostLoadAnalyzePolicy.IF_ROWS,
    )
    batches = [
        _batch(
            layer_name="roads",
            start_feature=0,
            feature_ids=["r1", "r2"],
            geometries=[
                LineString([(0, 0), (10, 10)]),
                LineString([(20, 20), (30, 30)]),
            ],
            road_classes=["residential", "secondary"],
        ),
        _batch(
            layer_name="roads",
            start_feature=2,
            feature_ids=["r3"],
            geometries=[LineString([(40, 40), (50, 50)])],
            road_classes=["primary"],
        ),
    ]

    result = writer.replace(
        dataset_version_id=version_id,
        layer=CanonicalSourceLayer.ROADS,
        batches=batches,
    )

    assert result.inserted_rows == 3
    assert result.deleted_rows == 0
    assert result.input_batches == 2
    assert result.insert_statements == 3
    assert result.working_srid == WORKING_SRID
    assert result.analyzed is True

    with _session_factory() as session:
        rows = session.execute(
            text(
                "SELECT source_feature_id, road_class, attributes_json, "
                "ST_SRID(geometry), GeometryType(geometry) "
                "FROM source_roads WHERE dataset_version_id = :version_id "
                "ORDER BY source_feature_id"
            ),
            {"version_id": version_id},
        ).all()

    assert [row[0] for row in rows] == ["r1", "r2", "r3"]
    assert [row[1] for row in rows] == ["residential", "secondary", "primary"]
    assert [row[2]["source_tag"] for row in rows] == ["tag-r1", "tag-r2", "tag-r3"]
    assert all(row[3] == WORKING_SRID for row in rows)
    assert all(row[4] == "MULTILINESTRING" for row in rows)


def test_retry_atomically_replaces_previous_layer_rows() -> None:
    _project_id, version_id = _create_dataset_version()
    writer = SqlAlchemySourceLayerBatchWriter(session_factory=_session_factory)

    first = _batch(
        layer_name="roads",
        start_feature=0,
        feature_ids=["old"],
        geometries=[LineString([(0, 0), (1, 1)])],
        road_classes=["residential"],
    )
    writer.replace(
        dataset_version_id=version_id,
        layer=CanonicalSourceLayer.ROADS,
        batches=[first],
    )

    retry = _batch(
        layer_name="roads",
        start_feature=0,
        feature_ids=["new-1", "new-2"],
        geometries=[
            LineString([(2, 2), (3, 3)]),
            LineString([(4, 4), (5, 5)]),
        ],
        road_classes=["secondary", "primary"],
    )
    result = writer.replace(
        dataset_version_id=version_id,
        layer=CanonicalSourceLayer.ROADS,
        batches=[retry],
    )

    assert result.deleted_rows == 1
    assert result.inserted_rows == 2

    with _session_factory() as session:
        ids = session.execute(
            text(
                "SELECT source_feature_id FROM source_roads "
                "WHERE dataset_version_id = :version_id ORDER BY source_feature_id"
            ),
            {"version_id": version_id},
        ).scalars().all()
    assert ids == ["new-1", "new-2"]


def test_failed_retry_rolls_back_delete_and_partial_inserts() -> None:
    _project_id, version_id = _create_dataset_version()
    writer = SqlAlchemySourceLayerBatchWriter(session_factory=_session_factory)
    stable = _batch(
        layer_name="roads",
        start_feature=0,
        feature_ids=["stable"],
        geometries=[LineString([(0, 0), (1, 1)])],
        road_classes=["residential"],
    )
    writer.replace(
        dataset_version_id=version_id,
        layer=CanonicalSourceLayer.ROADS,
        batches=[stable],
    )

    valid = _batch(
        layer_name="roads",
        start_feature=0,
        feature_ids=["partial"],
        geometries=[LineString([(10, 10), (11, 11)])],
        road_classes=["secondary"],
    )
    wrong_srid = _batch(
        layer_name="roads",
        start_feature=1,
        feature_ids=["bad"],
        geometries=[LineString([(1, 1), (2, 2)])],
        road_classes=["primary"],
        crs="EPSG:3857",
        working_srid=3857,
    )

    with pytest.raises(SourceLayerPersistenceError, match="working_srid"):
        writer.replace(
            dataset_version_id=version_id,
            layer=CanonicalSourceLayer.ROADS,
            batches=[valid, wrong_srid],
        )

    with _session_factory() as session:
        ids = session.execute(
            text(
                "SELECT source_feature_id FROM source_roads "
                "WHERE dataset_version_id = :version_id ORDER BY source_feature_id"
            ),
            {"version_id": version_id},
        ).scalars().all()
    assert ids == ["stable"]


def test_ready_dataset_version_rejects_replacement_before_mutation() -> None:
    _project_id, version_id = _create_dataset_version(status="processing")
    writer = SqlAlchemySourceLayerBatchWriter(session_factory=_session_factory)
    batch = _batch(
        layer_name="roads",
        start_feature=0,
        feature_ids=["stable"],
        geometries=[LineString([(0, 0), (1, 1)])],
        road_classes=["residential"],
    )
    writer.replace(
        dataset_version_id=version_id,
        layer=CanonicalSourceLayer.ROADS,
        batches=[batch],
    )

    with _session_factory() as session:
        with session.begin():
            version = session.get(DatasetVersion, version_id)
            assert version is not None
            version.status = "ready"

    with pytest.raises(SourceLayerImmutableError):
        writer.replace(
            dataset_version_id=version_id,
            layer=CanonicalSourceLayer.ROADS,
            batches=[],
        )

    with _session_factory() as session:
        count = session.scalar(
            text(
                "SELECT count(*) FROM source_roads "
                "WHERE dataset_version_id = :version_id"
            ),
            {"version_id": version_id},
        )
    assert count == 1


def test_writer_rejects_geometry_incompatible_with_canonical_table() -> None:
    _project_id, version_id = _create_dataset_version()
    writer = SqlAlchemySourceLayerBatchWriter(session_factory=_session_factory)
    polygon_batch = _batch(
        layer_name="roads",
        start_feature=0,
        feature_ids=["wrong"],
        geometries=[Polygon([(0, 0), (5, 0), (5, 5), (0, 0)])],
        road_classes=["residential"],
    )

    with pytest.raises(SourceLayerPersistenceError, match="canonical line layer"):
        writer.replace(
            dataset_version_id=version_id,
            layer=CanonicalSourceLayer.ROADS,
            batches=[polygon_batch],
        )

    with _session_factory() as session:
        count = session.scalar(
            text(
                "SELECT count(*) FROM source_roads "
                "WHERE dataset_version_id = :version_id"
            ),
            {"version_id": version_id},
        )
    assert count == 0


def test_never_analyze_policy_skips_post_load_analyze() -> None:
    _project_id, version_id = _create_dataset_version()
    writer = SqlAlchemySourceLayerBatchWriter(
        session_factory=_session_factory,
        analyze_policy=PostLoadAnalyzePolicy.NEVER,
    )
    batch = _batch(
        layer_name="roads",
        start_feature=0,
        feature_ids=["r1"],
        geometries=[LineString([(0, 0), (1, 1)])],
        road_classes=["residential"],
    )

    result = writer.replace(
        dataset_version_id=version_id,
        layer=CanonicalSourceLayer.ROADS,
        batches=[batch],
    )
    assert result.analyzed is False
