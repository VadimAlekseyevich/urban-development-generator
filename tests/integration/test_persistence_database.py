import uuid

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generated_entity import GeneratedZone
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceRoad

WORKING_SRID = 32637
COMMIT_SHA = "a" * 40

SOURCE_TABLES = (
    "source_roads",
    "source_buildings",
    "source_landuse",
    "source_water",
    "source_facilities",
    "source_constraints",
)
GENERATED_TABLES = (
    "generated_zones",
    "generated_roads",
    "generated_blocks",
    "generated_parcels",
    "generated_buildings",
    "generated_infrastructure",
)


def _migrate_to_head() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")


def _truncate_persistence_state() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture()
def db_session(migrated_database: None):
    _truncate_persistence_state()
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
    _truncate_persistence_state()


def _create_project(session: Session) -> Project:
    project = Project(
        name="Persistence integration",
        working_srid=WORKING_SRID,
        boundary_metadata={},
    )
    session.add(project)
    session.commit()
    return project


def _create_dataset_version(session: Session, project: Project) -> DatasetVersion:
    dataset = Dataset(project_id=project.id, kind="roads")
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        status="uploaded",
        source_metadata={},
    )
    session.add(version)
    session.commit()
    return version


def _create_run(session: Session, project: Project) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        status="queued",
        mode="EXPANSION",
        seed=42,
        working_srid=WORKING_SRID,
        config_json={},
        config_schema_version="test-v1",
    )
    session.add(run)
    session.commit()
    return run


def _mark_run_succeeded(session: Session, run: GenerationRun) -> None:
    run.commit_sha = COMMIT_SHA
    run.status = "succeeded"
    session.commit()


def test_empty_database_upgrades_to_current_postgis_schema(db_session: Session) -> None:
    revision = db_session.scalar(text("SELECT version_num FROM alembic_version"))
    postgis_version = db_session.scalar(text("SELECT PostGIS_Version()"))
    tables = {
        row[0]
        for row in db_session.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = current_schema()"
            )
        )
    }

    assert revision == "0016_building_persistence"
    assert isinstance(postgis_version, str) and postgis_version
    assert {
        "projects",
        "dataset_versions",
        "generation_runs",
        "jobs",
        *SOURCE_TABLES,
        *GENERATED_TABLES,
    } <= tables


def test_foreign_keys_and_job_idempotency_are_database_enforced(
    db_session: Session,
) -> None:
    missing_project_job = Job(
        project_id=uuid.uuid4(),
        job_type="normalize_dataset",
        idempotency_key="dataset-version-1",
    )
    db_session.add(missing_project_job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    project = _create_project(db_session)
    first = Job(
        project_id=project.id,
        job_type="normalize_dataset",
        idempotency_key="dataset-version-1",
    )
    db_session.add(first)
    db_session.commit()

    duplicate = Job(
        project_id=project.id,
        job_type="normalize_dataset",
        idempotency_key="dataset-version-1",
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    persisted_count = db_session.scalar(
        text(
            "SELECT count(*) FROM jobs "
            "WHERE project_id = :project_id "
            "AND job_type = 'normalize_dataset' "
            "AND idempotency_key = 'dataset-version-1'"
        ),
        {"project_id": project.id},
    )
    assert persisted_count == 1


def test_successful_generation_run_is_immutable_in_database(db_session: Session) -> None:
    project = _create_project(db_session)
    run = _create_run(db_session, project)
    _mark_run_succeeded(db_session, run)

    with pytest.raises(DBAPIError):
        db_session.execute(
            text("UPDATE generation_runs SET seed = seed + 1 WHERE id = :run_id"),
            {"run_id": run.id},
        )
        db_session.commit()
    db_session.rollback()

    persisted_seed = db_session.scalar(
        text("SELECT seed FROM generation_runs WHERE id = :run_id"),
        {"run_id": run.id},
    )
    assert persisted_seed == 42


def test_completed_generated_and_source_rows_are_immutable(db_session: Session) -> None:
    project = _create_project(db_session)
    run = _create_run(db_session, project)
    generated = GeneratedZone(
        run_id=run.id,
        attributes_json={},
        zone_class="residential",
        area_m2=100.0,
        diagnostics_json={},
        geometry=WKTElement(
            "MULTIPOLYGON(((0 0, 10 0, 10 10, 0 10, 0 0)))",
            srid=WORKING_SRID,
        ),
    )
    db_session.add(generated)
    db_session.commit()
    _mark_run_succeeded(db_session, run)

    with pytest.raises(DBAPIError):
        db_session.execute(
            text("DELETE FROM generated_zones WHERE id = :entity_id"),
            {"entity_id": generated.id},
        )
        db_session.commit()
    db_session.rollback()

    version = _create_dataset_version(db_session, project)
    source = SourceRoad(
        dataset_version_id=version.id,
        source_feature_id="road-1",
        attributes_json={},
        road_class="residential",
        one_way=False,
        geometry=WKTElement(
            "MULTILINESTRING((0 0, 10 10))",
            srid=WORKING_SRID,
        ),
    )
    db_session.add(source)
    db_session.commit()

    version.status = "ready"
    db_session.commit()

    with pytest.raises(DBAPIError):
        db_session.execute(
            text(
                "UPDATE source_roads SET road_class = 'secondary' "
                "WHERE id = :entity_id"
            ),
            {"entity_id": source.id},
        )
        db_session.commit()
    db_session.rollback()

    generated_count = db_session.scalar(
        text("SELECT count(*) FROM generated_zones WHERE id = :entity_id"),
        {"entity_id": generated.id},
    )
    persisted_road_class = db_session.scalar(
        text("SELECT road_class FROM source_roads WHERE id = :entity_id"),
        {"entity_id": source.id},
    )
    assert generated_count == 1
    assert persisted_road_class == "residential"


@pytest.mark.parametrize(
    ("table_name", "scope_column"),
    tuple((table_name, "dataset_version_id") for table_name in SOURCE_TABLES)
    + tuple((table_name, "run_id") for table_name in GENERATED_TABLES),
)
def test_spatial_access_indexes_are_installed_in_postgres(
    db_session: Session,
    table_name: str,
    scope_column: str,
) -> None:
    rows = db_session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = :table_name"
        ),
        {"table_name": table_name},
    ).all()
    indexes = {name: definition.lower() for name, definition in rows}

    scope_index = f"ix_{table_name}_{scope_column}_id"
    geometry_index = f"ix_{table_name}_geometry"

    assert scope_index in indexes
    assert f"using btree ({scope_column}, id)" in indexes[scope_index]
    assert geometry_index in indexes
    assert "using gist (geometry)" in indexes[geometry_index]
