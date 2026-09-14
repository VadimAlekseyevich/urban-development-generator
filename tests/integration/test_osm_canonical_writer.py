import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.db.source_layer_writer import SqlAlchemySourceLayerBatchWriter
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project
from backend.app.services.osm_canonical_writer import OsmCanonicalWriter
from backend.app.services.osm_mapping import DEFAULT_OSM_MAPPING_RULESET, OsmTagMapper
from backend.app.services.osm_pbf_reader import (
    OsmFeatureCategory,
    OsmPbfReader,
    OsmPbfReadLimits,
)
from core.urban_generator.domain import WorkingCRS

FIXTURE = Path("tests/fixtures/osm/minimal.pbf")
WORKING_SRID = 3857


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


def _create_dataset_version() -> uuid.UUID:
    with _session_factory() as session:
        with session.begin():
            project = Project(
                name="OSM canonical writer",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            dataset = Dataset(project_id=project.id, kind="osm")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="processing",
                source_metadata={"format": "osm_pbf"},
            )
            session.add(version)
            session.flush()
            return version.id


def test_real_pbf_mapping_persists_all_supported_canonical_layers() -> None:
    version_id = _create_dataset_version()
    reader = OsmPbfReader(
        limits=OsmPbfReadLimits(batch_size=2, max_features_per_layer=20)
    )
    mapper = OsmTagMapper()
    writer = OsmCanonicalWriter(
        batch_writer=SqlAlchemySourceLayerBatchWriter(session_factory=_session_factory)
    )
    expected_counts = {
        OsmFeatureCategory.ROADS: 1,
        OsmFeatureCategory.BUILDINGS: 1,
        OsmFeatureCategory.POI: 1,
        OsmFeatureCategory.LANDUSE: 1,
        OsmFeatureCategory.WATER: 2,
    }

    for category, expected_count in expected_counts.items():
        target_layer = DEFAULT_OSM_MAPPING_RULESET.for_category(category).layer
        mapped_batches = (
            mapper.map_batch(batch)
            for batch in reader.iter_batches(FIXTURE, categories=(category,))
        )
        result = writer.replace_layer(
            dataset_version_id=version_id,
            target_layer=target_layer,
            working_crs=WorkingCRS(WORKING_SRID),
            mapping_version=mapper.ruleset_version,
            batches=mapped_batches,
        )
        assert result.source_features == expected_count
        assert result.persistence.inserted_rows == expected_count
        assert result.persistence.working_srid == WORKING_SRID

    with _session_factory() as session:
        road = session.execute(
            text(
                "SELECT source_feature_id, road_class, one_way, one_way_direction, "
                "bridge, tunnel, layer, ST_SRID(geometry), GeometryType(geometry), "
                "attributes_json ->> 'osm_mapping_version' "
                "FROM source_roads WHERE dataset_version_id = :version_id"
            ),
            {"version_id": version_id},
        ).one()
        building = session.execute(
            text(
                "SELECT source_feature_id, building_class, ST_SRID(geometry), "
                "GeometryType(geometry) "
                "FROM source_buildings WHERE dataset_version_id = :version_id"
            ),
            {"version_id": version_id},
        ).one()
        facility = session.execute(
            text(
                "SELECT source_feature_id, facility_class, ST_SRID(geometry), "
                "GeometryType(geometry) "
                "FROM source_facilities WHERE dataset_version_id = :version_id"
            ),
            {"version_id": version_id},
        ).one()
        landuse = session.execute(
            text(
                "SELECT source_feature_id, landuse_class, ST_SRID(geometry), "
                "GeometryType(geometry) "
                "FROM source_landuse WHERE dataset_version_id = :version_id"
            ),
            {"version_id": version_id},
        ).one()
        water = session.execute(
            text(
                "SELECT source_feature_id, water_class, ST_SRID(geometry) "
                "FROM source_water WHERE dataset_version_id = :version_id "
                "ORDER BY source_feature_id"
            ),
            {"version_id": version_id},
        ).all()

    assert road == (
        "osm:way:100",
        "local",
        False,
        "both",
        False,
        False,
        0,
        WORKING_SRID,
        "MULTILINESTRING",
        "osm-v1",
    )
    assert building[0] == "osm:way:101"
    assert building[1] == "other"
    assert building[2] == WORKING_SRID
    assert building[3] == "MULTIPOLYGON"
    assert facility[0] == "osm:node:1"
    assert facility[1] == "education"
    assert facility[2] == WORKING_SRID
    assert facility[3] == "POINT"
    assert landuse[0] == "osm:way:102"
    assert landuse[2] == WORKING_SRID
    assert landuse[3] == "MULTIPOLYGON"
    assert [row[0] for row in water] == ["osm:way:103", "osm:way:104"]
    assert all(row[2] == WORKING_SRID for row in water)


def test_osm_layer_retry_replaces_only_that_canonical_layer() -> None:
    version_id = _create_dataset_version()
    reader = OsmPbfReader(
        limits=OsmPbfReadLimits(batch_size=1, max_features_per_layer=20)
    )
    mapper = OsmTagMapper()
    writer = OsmCanonicalWriter(
        batch_writer=SqlAlchemySourceLayerBatchWriter(session_factory=_session_factory)
    )
    target_layer = DEFAULT_OSM_MAPPING_RULESET.for_category(OsmFeatureCategory.ROADS).layer

    def road_batches():
        return (
            mapper.map_batch(batch)
            for batch in reader.iter_batches(FIXTURE, categories=(OsmFeatureCategory.ROADS,))
        )

    first = writer.replace_layer(
        dataset_version_id=version_id,
        target_layer=target_layer,
        working_crs=WorkingCRS(WORKING_SRID),
        mapping_version=mapper.ruleset_version,
        batches=road_batches(),
    )
    retry = writer.replace_layer(
        dataset_version_id=version_id,
        target_layer=target_layer,
        working_crs=WorkingCRS(WORKING_SRID),
        mapping_version=mapper.ruleset_version,
        batches=road_batches(),
    )

    assert first.persistence.deleted_rows == 0
    assert first.persistence.inserted_rows == 1
    assert retry.persistence.deleted_rows == 1
    assert retry.persistence.inserted_rows == 1

    with _session_factory() as session:
        count = session.scalar(
            text(
                "SELECT count(*) FROM source_roads "
                "WHERE dataset_version_id = :version_id"
            ),
            {"version_id": version_id},
        )
    assert count == 1
