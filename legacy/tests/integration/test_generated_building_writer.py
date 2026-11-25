import uuid

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import box
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.db.generated_building_writer import (
    GeneratedBuildingImmutableError,
    GeneratedBuildingPersistenceError,
    SqlAlchemyGeneratedBuildingWriter,
)
from backend.app.db.session import SessionLocal, engine
from backend.app.models.generated_entity import (
    GeneratedBlock,
    GeneratedBuilding,
    GeneratedParcel,
)
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaMetricsCalculator,
    BuildingAreaSubject,
    BuildingAttributeAssignmentResult,
    BuildingUse,
)
from core.urban_generator.zoning import ZoneClass

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
        name="Generated building persistence",
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


def _create_block(
    session: Session,
    *,
    run_id: uuid.UUID,
    block_key: str,
    x_offset: float = 0.0,
) -> GeneratedBlock:
    block = GeneratedBlock(
        id=uuid.uuid5(run_id, f"generated-block:{block_key}"),
        run_id=run_id,
        geometry=from_shape(
            box(x_offset, 0.0, x_offset + 100.0, 100.0),
            srid=WORKING_SRID,
        ),
        block_key=block_key,
        area_m2=10_000.0,
        association_status=None,
        attributes_json={},
    )
    session.add(block)
    session.commit()
    return block


def _create_parcel(
    session: Session,
    *,
    run_id: uuid.UUID,
    block_id: uuid.UUID,
    parcel_key: str,
) -> GeneratedParcel:
    parcel = GeneratedParcel(
        id=uuid.uuid5(run_id, f"generated-parcel:{parcel_key}"),
        run_id=run_id,
        geometry=from_shape(box(0.0, 0.0, 50.0, 100.0), srid=WORKING_SRID),
        parcel_key=parcel_key,
        block_id=block_id,
        area_m2=5_000.0,
        buildable_area_m2=4_000.0,
        frontage_m=50.0,
        buildable_geometry=from_shape(
            box(5.0, 5.0, 45.0, 95.0),
            srid=WORKING_SRID,
        ),
        attributes_json={},
    )
    session.add(parcel)
    session.commit()
    return parcel


def _assigned(
    building_id: str,
    *,
    source_id: str,
    archetype: BuildingArchetype,
    use: BuildingUse,
    floors: int,
) -> AssignedBuildingAttributes:
    return AssignedBuildingAttributes(
        building_id=building_id,
        source_id=source_id,
        zone_class=ZoneClass.RESIDENTIAL,
        archetype=archetype,
        use=use,
        floors=floors,
        config_version="attrs-v1",
    )


def _results(
    specs: tuple[
        tuple[
            str,
            str,
            object,
            BuildingArchetype,
            BuildingUse,
            int,
        ],
        ...,
    ],
    *,
    working_srid: int = WORKING_SRID,
):
    assignments = tuple(
        sorted(
            (
                _assigned(
                    building_id,
                    source_id=source_id,
                    archetype=archetype,
                    use=use,
                    floors=floors,
                )
                for (
                    building_id,
                    source_id,
                    _geometry,
                    archetype,
                    use,
                    floors,
                ) in specs
            ),
            key=lambda item: item.building_id,
        )
    )
    by_id = {item.building_id: item for item in assignments}
    subjects = tuple(
        BuildingAreaSubject(
            building_id=building_id,
            geometry=geometry,
            attributes=by_id[building_id],
            working_srid=working_srid,
        )
        for (
            building_id,
            _source_id,
            geometry,
            _archetype,
            _use,
            _floors,
        ) in specs
    )
    assignment = BuildingAttributeAssignmentResult(
        config_version="attrs-v1",
        config_fingerprint="a" * 64,
        buildings=assignments,
    )
    area_result = BuildingAreaMetricsCalculator(
        working_srid=working_srid,
    ).calculate(
        subjects,
        site_area_m2=20_000.0,
    )
    return subjects, assignment, area_result


def _writer() -> SqlAlchemyGeneratedBuildingWriter:
    return SqlAlchemyGeneratedBuildingWriter(
        session_factory=SessionLocal,
        max_insert_rows=1,
    )


def test_writer_persists_typed_building_refs_attributes_and_metrics(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    block_a = _create_block(
        db_session,
        run_id=run.id,
        block_key="block:a",
    )
    block_b = _create_block(
        db_session,
        run_id=run.id,
        block_key="block:b",
        x_offset=200.0,
    )
    parcel = _create_parcel(
        db_session,
        run_id=run.id,
        block_id=block_a.id,
        parcel_key="parcel:a",
    )
    subjects, assignment, area_result = _results(
        (
            (
                "building:parcel",
                "parcel:a",
                box(10.0, 10.0, 20.0, 20.0),
                BuildingArchetype.POINT,
                BuildingUse.RESIDENTIAL,
                3,
            ),
            (
                "building:block",
                "block:b",
                box(220.0, 10.0, 240.0, 20.0),
                BuildingArchetype.BAR,
                BuildingUse.MIXED,
                5,
            ),
        )
    )

    result = _writer().replace(
        run_id=run.id,
        subjects=subjects,
        assignment=assignment,
        area_result=area_result,
    )

    assert result.deleted_rows == 0
    assert result.inserted_rows == 2
    assert result.insert_statements == 2
    assert result.block_ref_count == 2
    assert result.parcel_ref_count == 1

    rows = db_session.scalars(
        select(GeneratedBuilding)
        .where(GeneratedBuilding.run_id == run.id)
        .order_by(GeneratedBuilding.building_key)
    ).all()
    assert [row.building_key for row in rows] == [
        "building:block",
        "building:parcel",
    ]

    block_row, parcel_row = rows
    assert block_row.block_id == block_b.id
    assert block_row.parcel_id is None
    assert block_row.source_id == "block:b"
    assert block_row.zone_class == ZoneClass.RESIDENTIAL.value
    assert block_row.archetype == BuildingArchetype.BAR.value
    assert block_row.building_use == BuildingUse.MIXED.value
    assert block_row.floors == 5
    assert block_row.footprint_area_m2 == pytest.approx(200.0)
    assert block_row.gfa_m2 == pytest.approx(1000.0)
    assert block_row.attributes_json["source_kind"] == "block"

    assert parcel_row.block_id == block_a.id
    assert parcel_row.parcel_id == parcel.id
    assert parcel_row.source_id == "parcel:a"
    assert parcel_row.archetype == BuildingArchetype.POINT.value
    assert parcel_row.building_use == BuildingUse.RESIDENTIAL.value
    assert parcel_row.floors == 3
    assert parcel_row.footprint_area_m2 == pytest.approx(100.0)
    assert parcel_row.gfa_m2 == pytest.approx(300.0)
    assert parcel_row.attributes_json["source_kind"] == "parcel"
    assert parcel_row.attributes_json["attribute_config_fingerprint"] == "a" * 64
    assert to_shape(parcel_row.geometry).equals(box(10.0, 10.0, 20.0, 20.0))


def test_retry_replaces_buildings_and_preserves_deterministic_ids(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    block = _create_block(db_session, run_id=run.id, block_key="block:a")
    _create_parcel(
        db_session,
        run_id=run.id,
        block_id=block.id,
        parcel_key="parcel:a",
    )
    subjects, assignment, area_result = _results(
        (
            (
                "building:a",
                "parcel:a",
                box(10.0, 10.0, 20.0, 20.0),
                BuildingArchetype.POINT,
                BuildingUse.RESIDENTIAL,
                2,
            ),
        )
    )
    writer = _writer()
    writer.replace(
        run_id=run.id,
        subjects=subjects,
        assignment=assignment,
        area_result=area_result,
    )
    first_id = db_session.scalar(
        select(GeneratedBuilding.id).where(GeneratedBuilding.run_id == run.id)
    )

    result = writer.replace(
        run_id=run.id,
        subjects=subjects,
        assignment=assignment,
        area_result=area_result,
    )
    db_session.expire_all()
    second_id = db_session.scalar(
        select(GeneratedBuilding.id).where(GeneratedBuilding.run_id == run.id)
    )

    assert result.deleted_rows == 1
    assert result.inserted_rows == 1
    assert first_id == second_id


def test_invalid_source_ref_fails_before_replacing_existing_rows(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    block = _create_block(db_session, run_id=run.id, block_key="block:a")
    _create_parcel(
        db_session,
        run_id=run.id,
        block_id=block.id,
        parcel_key="parcel:a",
    )
    good = _results(
        (
            (
                "building:a",
                "parcel:a",
                box(10.0, 10.0, 20.0, 20.0),
                BuildingArchetype.POINT,
                BuildingUse.RESIDENTIAL,
                2,
            ),
        )
    )
    writer = _writer()
    writer.replace(
        run_id=run.id,
        subjects=good[0],
        assignment=good[1],
        area_result=good[2],
    )
    original_id = db_session.scalar(
        select(GeneratedBuilding.id).where(GeneratedBuilding.run_id == run.id)
    )

    bad = _results(
        (
            (
                "building:bad",
                "parcel:missing",
                box(30.0, 10.0, 40.0, 20.0),
                BuildingArchetype.POINT,
                BuildingUse.RESIDENTIAL,
                2,
            ),
        )
    )
    with pytest.raises(
        GeneratedBuildingPersistenceError,
        match="does not resolve",
    ):
        writer.replace(
            run_id=run.id,
            subjects=bad[0],
            assignment=bad[1],
            area_result=bad[2],
        )

    assert db_session.scalar(
        select(GeneratedBuilding.id).where(GeneratedBuilding.run_id == run.id)
    ) == original_id


def test_writer_rejects_successful_run_without_touching_rows(
    db_session: Session,
) -> None:
    run = _create_run(db_session, status="succeeded")
    subjects, assignment, area_result = _results(
        (
            (
                "building:a",
                "block:a",
                box(10.0, 10.0, 20.0, 20.0),
                BuildingArchetype.POINT,
                BuildingUse.RESIDENTIAL,
                2,
            ),
        )
    )

    with pytest.raises(GeneratedBuildingImmutableError, match="successful"):
        _writer().replace(
            run_id=run.id,
            subjects=subjects,
            assignment=assignment,
            area_result=area_result,
        )

    assert db_session.scalar(
        select(func.count())
        .select_from(GeneratedBuilding)
        .where(GeneratedBuilding.run_id == run.id)
    ) == 0


def test_writer_rejects_result_crs_mismatch(db_session: Session) -> None:
    run = _create_run(db_session)
    subjects, assignment, area_result = _results(
        (
            (
                "building:a",
                "block:a",
                box(10.0, 10.0, 20.0, 20.0),
                BuildingArchetype.POINT,
                BuildingUse.RESIDENTIAL,
                2,
            ),
        ),
        working_srid=3857,
    )

    with pytest.raises(
        GeneratedBuildingPersistenceError,
        match="working_srid",
    ):
        _writer().replace(
            run_id=run.id,
            subjects=subjects,
            assignment=assignment,
            area_result=area_result,
        )


def test_generated_building_semantic_indexes_exist(db_session: Session) -> None:
    indexes = set(
        db_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND tablename = 'generated_buildings'"
            )
        ).all()
    )

    assert "uq_generated_buildings_run_building_key" in indexes
    assert "ix_generated_buildings_run_block_id" in indexes
    assert "ix_generated_buildings_run_parcel_id" in indexes
    assert "ix_generated_buildings_run_source_id" in indexes
    assert "ix_generated_buildings_run_use" in indexes
    assert "ix_generated_buildings_run_archetype" in indexes
    assert "ix_generated_buildings_geometry" in indexes
