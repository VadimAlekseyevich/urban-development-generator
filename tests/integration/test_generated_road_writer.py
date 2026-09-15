import uuid

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import LineString, MultiLineString
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.db.generated_road_writer import (
    GeneratedRoadImmutableError,
    GeneratedRoadPersistenceError,
    SqlAlchemyGeneratedRoadWriter,
)
from backend.app.db.session import SessionLocal, engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generated_entity import GeneratedRoad
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceRoad
from core.urban_generator.domain import WorldStateContract
from core.urban_generator.roads import (
    NodedRoad,
    RoadClassificationOrigin,
    RoadClassificationSubject,
    RoadGraphBuilder,
    RoadGraphInput,
    RuleBasedRoadClassifier,
)

WORKING_SRID = 32637


def _migrate_to_head() -> None:
    command.upgrade(Config("alembic.ini"), "head")


def _truncate_state() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture()
def db_session(migrated_database: None):
    _truncate_state()
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
    _truncate_state()


def _create_run(session: Session, *, status: str = "running") -> GenerationRun:
    project = Project(
        name="Generated road persistence",
        working_srid=WORKING_SRID,
        boundary_metadata={},
    )
    session.add(project)
    session.flush()
    run = GenerationRun(
        project_id=project.id,
        status=status,
        mode="FROM_SCRATCH",
        seed=42,
        working_srid=WORKING_SRID,
        config_json={},
        config_schema_version="test-v1",
        commit_sha="a" * 40 if status == "succeeded" else None,
    )
    session.add(run)
    session.commit()
    return run


def _create_source_road(session: Session, *, project_id: uuid.UUID) -> SourceRoad:
    dataset = Dataset(project_id=project_id, kind="roads")
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        status="ready",
        source_metadata={},
    )
    session.add(version)
    session.flush()
    road = SourceRoad(
        dataset_version_id=version.id,
        source_feature_id="source-road-1",
        road_class="primary",
        name="Source road",
        lanes=2,
        max_speed_kph=50.0,
        one_way=False,
        one_way_direction="both",
        bridge=False,
        tunnel=False,
        layer=0,
        attributes_json={},
        geometry=from_shape(
            MultiLineString([[(0.0, 0.0), (10.0, 0.0)]]),
            srid=WORKING_SRID,
        ),
    )
    session.add(road)
    session.commit()
    return road


def _graph_and_classification(run_id: uuid.UUID):
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            RoadGraphInput(
                road=NodedRoad(
                    road_id="source",
                    parts=(LineString(((0.0, 0.0), (10.0, 0.0))),),
                ),
                state=WorldStateContract.fixed_source(),
            ),
            RoadGraphInput(
                road=NodedRoad(
                    road_id="generated",
                    parts=(LineString(((10.0, 0.0), (20.0, 0.0))),),
                ),
                state=WorldStateContract.generated_for(run_id),
            ),
        )
    )
    classification = RuleBasedRoadClassifier().classify(
        (
            RoadClassificationSubject(
                road_id="source",
                origin=RoadClassificationOrigin.EXISTING,
                existing_class="primary",
            ),
            RoadClassificationSubject(
                road_id="generated",
                origin=RoadClassificationOrigin.BASELINE_GENERATED,
                length_m=10.0,
            ),
        )
    )
    return graph, classification


def _writer() -> SqlAlchemyGeneratedRoadWriter:
    return SqlAlchemyGeneratedRoadWriter(session_factory=SessionLocal, max_insert_rows=1)


def test_writer_persists_only_generated_edges_with_classification_and_source_refs(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    source_road = _create_source_road(db_session, project_id=run.project_id)
    graph, classification = _graph_and_classification(run.id)

    result = _writer().replace(
        run_id=run.id,
        graph=graph,
        classification=classification,
        source_road_ids_by_road_id={"generated": (source_road.id,)},
    )

    assert result.deleted_rows == 0
    assert result.inserted_rows == 1
    assert result.insert_statements == 1
    assert result.source_ref_count == 1

    rows = db_session.scalars(
        select(GeneratedRoad).where(GeneratedRoad.run_id == run.id)
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert to_shape(row.geometry).equals(LineString(((10.0, 0.0), (20.0, 0.0))))
    assert row.attributes_json["road_id"] == "generated"
    assert row.attributes_json["road_class"] == "collector"
    assert row.attributes_json["origin"] == "BASELINE_GENERATED"
    assert row.attributes_json["classification_reason"] == "BASELINE_FLOOR"
    assert row.attributes_json["source_road_ids"] == [str(source_road.id)]
    assert row.attributes_json["classification_strategy"] == {
        "name": "rule-based-road-classification",
        "version": "1",
    }


def test_writer_retry_atomically_replaces_previous_run_roads(db_session: Session) -> None:
    run = _create_run(db_session)
    graph, classification = _graph_and_classification(run.id)
    _writer().replace(run_id=run.id, graph=graph, classification=classification)

    result = _writer().replace(run_id=run.id, graph=graph, classification=classification)

    count = db_session.scalar(
        select(func.count()).select_from(GeneratedRoad).where(GeneratedRoad.run_id == run.id)
    )
    assert result.deleted_rows == 1
    assert result.inserted_rows == 1
    assert count == 1


def test_writer_rejects_successful_run_without_touching_rows(db_session: Session) -> None:
    run = _create_run(db_session, status="succeeded")
    graph, classification = _graph_and_classification(run.id)

    with pytest.raises(GeneratedRoadImmutableError, match="successful"):
        _writer().replace(run_id=run.id, graph=graph, classification=classification)

    count = db_session.scalar(
        select(func.count()).select_from(GeneratedRoad).where(GeneratedRoad.run_id == run.id)
    )
    assert count == 0


def test_writer_rejects_missing_source_road_reference(db_session: Session) -> None:
    run = _create_run(db_session)
    graph, classification = _graph_and_classification(run.id)
    missing = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    with pytest.raises(GeneratedRoadPersistenceError, match="source road refs do not exist"):
        _writer().replace(
            run_id=run.id,
            graph=graph,
            classification=classification,
            source_road_ids_by_road_id={"generated": (missing,)},
        )

    count = db_session.scalar(
        select(func.count()).select_from(GeneratedRoad).where(GeneratedRoad.run_id == run.id)
    )
    assert count == 0


def test_generated_road_semantic_indexes_exist(db_session: Session) -> None:
    names = set(
        db_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = 'generated_roads'"
            )
        ).all()
    )

    assert "ix_generated_roads_run_road_id" in names
    assert "ix_generated_roads_run_road_class" in names
    assert "ix_generated_roads_source_refs_gin" in names
