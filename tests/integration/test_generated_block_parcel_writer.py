import uuid

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import LineString, MultiPolygon, box
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.db.generated_block_parcel_writer import (
    GeneratedBlockParcelImmutableError,
    GeneratedBlockParcelPersistenceError,
    SqlAlchemyGeneratedBlockParcelWriter,
)
from backend.app.db.session import SessionLocal, engine
from backend.app.models.generated_entity import GeneratedBlock, GeneratedParcel, GeneratedZone
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.blocks import (
    BlockZoneAssociation,
    BlockZoneAssociationDiagnostics,
    BlockZoneAssociationResult,
    BlockZoneAssociationStatus,
    CleanedBlockCandidate,
    ParcelFrontageSegment,
    ParcelSubdivisionDecision,
    ParcelSubdivisionDiagnostics,
    ParcelSubdivisionPolicy,
    ParcelSubdivisionResult,
    PlanningParcel,
    SplitBlockCandidate,
    ZoneAssociatedBlock,
)
from core.urban_generator.domain import WorkingCRS
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
        name="Generated block parcel persistence",
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


def _create_zone(session: Session, *, run_id: uuid.UUID) -> GeneratedZone:
    zone = GeneratedZone(
        id=uuid.uuid4(),
        run_id=run_id,
        geometry=from_shape(MultiPolygon([box(-5, -5, 15, 15)]), srid=WORKING_SRID),
        zone_class=ZoneClass.RESIDENTIAL.value,
        area_m2=400.0,
        diagnostics_json={},
        attributes_json={},
    )
    session.add(zone)
    session.commit()
    return zone


def _results(
    *,
    zone_id: uuid.UUID,
    working_srid: int = WORKING_SRID,
) -> tuple[BlockZoneAssociationResult, ParcelSubdivisionResult]:
    member = SplitBlockCandidate(
        block_id="split:a",
        input_block_id="input:a",
        source_block_id="source:a",
        source_fragment_index=0,
        split_path=(),
        geometry=box(0, 0, 10, 10),
    )
    cleaned = CleanedBlockCandidate(
        block_id="block:a",
        members=(member,),
        geometry=box(0, 0, 10, 10),
    )
    association = BlockZoneAssociation(
        status=BlockZoneAssociationStatus.ASSOCIATED,
        zone_id=str(zone_id),
        zone_class=ZoneClass.RESIDENTIAL,
        positive_overlap_zone_ids=(str(zone_id),),
        maximum_overlap_ratio=1.0,
    )
    zoned = BlockZoneAssociationResult(
        working_crs=WorkingCRS(srid=working_srid),
        blocks=(ZoneAssociatedBlock(cleaned_block=cleaned, association=association),),
        diagnostics=BlockZoneAssociationDiagnostics(
            block_count=1,
            zone_count=1,
            associated_block_count=1,
            no_overlap_block_count=0,
            partial_overlap_block_count=0,
            ambiguous_block_count=0,
            spatial_candidate_pair_count=1,
            positive_overlap_pair_count=1,
        ),
    )

    first = PlanningParcel(
        parcel_id="parcel:a:0000",
        block_id="block:a",
        working_srid=working_srid,
        geometry=box(0, 0, 5, 10),
        buildable_envelope=box(0.5, 0.5, 4.5, 9.5),
        frontages=(
            ParcelFrontageSegment(
                road_id="road:front",
                geometry=LineString(((0, 0), (5, 0))),
            ),
        ),
        zone_id=str(zone_id),
        zone_class=ZoneClass.RESIDENTIAL,
    )
    second = PlanningParcel(
        parcel_id="parcel:a:0001",
        block_id="block:a",
        working_srid=working_srid,
        geometry=box(5, 0, 10, 10),
        buildable_envelope=box(5.5, 0.5, 9.5, 9.5),
        frontages=(
            ParcelFrontageSegment(
                road_id="road:front",
                geometry=LineString(((5, 0), (10, 0))),
            ),
        ),
        zone_id=str(zone_id),
        zone_class=ZoneClass.RESIDENTIAL,
    )
    subdivision = ParcelSubdivisionResult(
        working_crs=WorkingCRS(srid=working_srid),
        policy=ParcelSubdivisionPolicy(
            target_frontage_m=5.0,
            minimum_frontage_m=2.0,
            minimum_parcel_area_m2=10.0,
        ),
        parcels=(first, second),
        decisions=(
            ParcelSubdivisionDecision(
                block_id="block:a",
                parcel_ids=("parcel:a:0000", "parcel:a:0001"),
                skip_reason=None,
                selected_frontage_road_id="road:front",
                selected_frontage_length_m=10.0,
            ),
        ),
        diagnostics=ParcelSubdivisionDiagnostics(
            input_block_count=1,
            residential_associated_block_count=1,
            parceled_block_count=1,
            subdivided_block_count=1,
            single_parcel_block_count=0,
            skipped_block_count=0,
            parcel_count=2,
            road_edge_count=1,
            road_candidate_pair_count=1,
            frontage_overlap_pair_count=1,
            parceled_area_m2=100.0,
        ),
    )
    return zoned, subdivision


def _writer() -> SqlAlchemyGeneratedBlockParcelWriter:
    return SqlAlchemyGeneratedBlockParcelWriter(
        session_factory=SessionLocal,
        max_insert_rows=1,
    )


def test_writer_persists_typed_block_parcel_refs_and_geometry(db_session: Session) -> None:
    run = _create_run(db_session)
    zone = _create_zone(db_session, run_id=run.id)
    zoned, subdivision = _results(zone_id=zone.id)

    result = _writer().replace(
        run_id=run.id,
        zoned_blocks=zoned,
        subdivision=subdivision,
    )

    assert result.deleted_block_rows == 0
    assert result.deleted_parcel_rows == 0
    assert result.inserted_block_rows == 1
    assert result.inserted_parcel_rows == 2
    assert result.block_insert_statements == 1
    assert result.parcel_insert_statements == 2
    assert result.zone_ref_count == 1

    block = db_session.scalars(
        select(GeneratedBlock).where(GeneratedBlock.run_id == run.id)
    ).one()
    assert block.block_key == "block:a"
    assert block.zone_id == zone.id
    assert block.area_m2 == pytest.approx(100.0)
    assert block.association_status == BlockZoneAssociationStatus.ASSOCIATED.value
    assert block.attributes_json["member_block_ids"] == ["split:a"]
    assert block.attributes_json["subdivision"]["parcel_count"] == 2
    assert to_shape(block.geometry).equals(box(0, 0, 10, 10))

    parcels = db_session.scalars(
        select(GeneratedParcel)
        .where(GeneratedParcel.run_id == run.id)
        .order_by(GeneratedParcel.parcel_key)
    ).all()
    assert [parcel.parcel_key for parcel in parcels] == [
        "parcel:a:0000",
        "parcel:a:0001",
    ]
    assert {parcel.block_id for parcel in parcels} == {block.id}
    assert {parcel.zone_id for parcel in parcels} == {zone.id}
    assert [parcel.area_m2 for parcel in parcels] == pytest.approx([50.0, 50.0])
    assert [parcel.buildable_area_m2 for parcel in parcels] == pytest.approx([36.0, 36.0])
    assert [parcel.frontage_m for parcel in parcels] == pytest.approx([5.0, 5.0])
    assert all(to_shape(parcel.buildable_geometry).area == pytest.approx(36.0) for parcel in parcels)
    assert parcels[0].attributes_json["semantics"] == "planning_lot_non_cadastral"
    assert parcels[0].attributes_json["frontage_road_ids"] == ["road:front"]


def test_retry_replaces_rows_but_preserves_deterministic_database_ids(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    zone = _create_zone(db_session, run_id=run.id)
    zoned, subdivision = _results(zone_id=zone.id)
    writer = _writer()
    writer.replace(run_id=run.id, zoned_blocks=zoned, subdivision=subdivision)

    first_block_ids = tuple(
        db_session.scalars(
            select(GeneratedBlock.id).where(GeneratedBlock.run_id == run.id)
        ).all()
    )
    first_parcel_ids = tuple(
        db_session.scalars(
            select(GeneratedParcel.id)
            .where(GeneratedParcel.run_id == run.id)
            .order_by(GeneratedParcel.parcel_key)
        ).all()
    )

    result = writer.replace(
        run_id=run.id,
        zoned_blocks=zoned,
        subdivision=subdivision,
    )
    db_session.expire_all()
    second_block_ids = tuple(
        db_session.scalars(
            select(GeneratedBlock.id).where(GeneratedBlock.run_id == run.id)
        ).all()
    )
    second_parcel_ids = tuple(
        db_session.scalars(
            select(GeneratedParcel.id)
            .where(GeneratedParcel.run_id == run.id)
            .order_by(GeneratedParcel.parcel_key)
        ).all()
    )

    assert result.deleted_block_rows == 1
    assert result.deleted_parcel_rows == 2
    assert first_block_ids == second_block_ids
    assert first_parcel_ids == second_parcel_ids


def test_invalid_zone_ref_fails_before_replacing_existing_rows(db_session: Session) -> None:
    run = _create_run(db_session)
    zone = _create_zone(db_session, run_id=run.id)
    good_zoned, good_subdivision = _results(zone_id=zone.id)
    writer = _writer()
    writer.replace(
        run_id=run.id,
        zoned_blocks=good_zoned,
        subdivision=good_subdivision,
    )
    block_id = db_session.scalar(
        select(GeneratedBlock.id).where(GeneratedBlock.run_id == run.id)
    )
    parcel_ids = tuple(
        db_session.scalars(
            select(GeneratedParcel.id)
            .where(GeneratedParcel.run_id == run.id)
            .order_by(GeneratedParcel.parcel_key)
        ).all()
    )

    missing_zone = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    bad_zoned, bad_subdivision = _results(zone_id=missing_zone)
    with pytest.raises(GeneratedBlockParcelPersistenceError, match="zone refs do not exist"):
        writer.replace(
            run_id=run.id,
            zoned_blocks=bad_zoned,
            subdivision=bad_subdivision,
        )

    assert db_session.scalar(
        select(GeneratedBlock.id).where(GeneratedBlock.run_id == run.id)
    ) == block_id
    assert tuple(
        db_session.scalars(
            select(GeneratedParcel.id)
            .where(GeneratedParcel.run_id == run.id)
            .order_by(GeneratedParcel.parcel_key)
        ).all()
    ) == parcel_ids


def test_writer_rejects_successful_run_without_touching_rows(db_session: Session) -> None:
    run = _create_run(db_session, status="succeeded")
    zone = _create_zone(db_session, run_id=run.id)
    zoned, subdivision = _results(zone_id=zone.id)

    with pytest.raises(GeneratedBlockParcelImmutableError, match="successful"):
        _writer().replace(
            run_id=run.id,
            zoned_blocks=zoned,
            subdivision=subdivision,
        )

    assert db_session.scalar(
        select(func.count()).select_from(GeneratedBlock).where(GeneratedBlock.run_id == run.id)
    ) == 0
    assert db_session.scalar(
        select(func.count()).select_from(GeneratedParcel).where(GeneratedParcel.run_id == run.id)
    ) == 0


def test_writer_rejects_result_crs_mismatch(db_session: Session) -> None:
    run = _create_run(db_session)
    zone = _create_zone(db_session, run_id=run.id)
    zoned, subdivision = _results(zone_id=zone.id, working_srid=3857)

    with pytest.raises(GeneratedBlockParcelPersistenceError, match="working_srid"):
        _writer().replace(
            run_id=run.id,
            zoned_blocks=zoned,
            subdivision=subdivision,
        )


def test_generated_block_parcel_semantic_indexes_exist(db_session: Session) -> None:
    block_indexes = set(
        db_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = 'generated_blocks'"
            )
        ).all()
    )
    parcel_indexes = set(
        db_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = 'generated_parcels'"
            )
        ).all()
    )

    assert "uq_generated_blocks_run_block_key" in block_indexes
    assert "ix_generated_blocks_run_zone_id" in block_indexes
    assert "ix_generated_blocks_run_association_status" in block_indexes
    assert "uq_generated_parcels_run_parcel_key" in parcel_indexes
    assert "ix_generated_parcels_run_block_id" in parcel_indexes
    assert "ix_generated_parcels_run_zone_id" in parcel_indexes
    assert "ix_generated_parcels_buildable_geometry" in parcel_indexes
