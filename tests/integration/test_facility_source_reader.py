"""Run-linked facilities are loaded from PostGIS, never synthetic defaults."""

from dataclasses import replace

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.adapters.facility_source_reader import (
    FacilitySourceReadError,
    SqlAlchemyFacilitySourceReader,
)
from backend.app.adapters.pipeline_context import SqlAlchemyPipelineContextAdapter
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceFacility
from core.urban_generator.domain import (
    ProjectRef,
    ProjectSettings,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)

SRID = 32637


def _session() -> Session:
    return Session(engine, expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def scenario(migrated_database):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    try:
        with _session() as session:
            project = Project(
                name="Facilities", working_srid=SRID, boundary_metadata={},
                boundary=WKTElement("MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0)))", srid=SRID),
            )
            session.add(project)
            session.flush()
            dataset = Dataset(project_id=project.id, kind="facilities")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id, version=1, status="processing",
                checksum_sha256="b" * 64, source_metadata={},
            )
            session.add(version)
            session.flush()
            for key, coord, capacity in (("a", 1, 30), ("b", 2, None), ("outside", 100, 5)):
                session.add(SourceFacility(
                    dataset_version_id=version.id, source_feature_id=key,
                    facility_class="school", capacity=capacity, attributes_json={},
                    geometry=WKTElement(f"POINT({coord} {coord})", srid=SRID),
                ))
            session.flush()
            version.status = "ready"
            session.flush()
            run = GenerationRun(
                project_id=project.id, working_srid=SRID, mode="EXPANSION", status="queued",
                seed=1, config_json={}, config_schema_version="fixture",
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
                facilities=(SnapshotLayerRef(
                    kind=SnapshotLayerKind.FACILITIES,
                    source_ref=f"dataset-version:{version.id}:v1",
                ),),
            )
        yield snapshot
    finally:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def test_read_fixed_capacity_and_filter_outside_boundary(scenario):
    reader = SqlAlchemyFacilitySourceReader(session_factory=_session)
    result = reader.read(snapshot=scenario)
    assert [item.source_feature_id for item in result] == ["a", "b"]
    assert [item.capacity for item in result] == [30, None]
    assert result[0].geometry.x == 1
    assert reader.read(snapshot=scenario) == result
    with _session() as session:
        assert session.query(SourceFacility).count() == 3


def test_omitted_and_excessive_facilities_fail_closed(scenario):
    reader = SqlAlchemyFacilitySourceReader(session_factory=_session)
    with pytest.raises(FacilitySourceReadError, match="ready run-linked"):
        reader.read(snapshot=replace(scenario, facilities=()))
    with pytest.raises(FacilitySourceReadError, match="feature budget"):
        SqlAlchemyFacilitySourceReader(session_factory=_session, max_features=1).read(
            snapshot=scenario,
        )
