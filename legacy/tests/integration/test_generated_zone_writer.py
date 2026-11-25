import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.shape import to_shape
from shapely.geometry import MultiPolygon, box
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.db.generated_zone_writer import (
    GeneratedZoneImmutableError,
    GeneratedZonePersistenceError,
    SqlAlchemyGeneratedZoneWriter,
)
from backend.app.db.session import SessionLocal, engine
from backend.app.models.generated_entity import GeneratedZone
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.domain.constraints import (
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    ValidationReport,
)
from core.urban_generator.zoning import (
    EvaluatedZoneConstraints,
    ZoneAssignment,
    ZoneAssignmentResult,
    ZoneClass,
    ZoneConstraintEvaluationResult,
    ZoneConstraintSubject,
    ZoneShareDiagnostic,
    ZoningPartitionCell,
    ZoningPartitionResult,
    ZoningSeed,
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
        name="Generated zone persistence",
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


def _make_partition(count: int) -> ZoningPartitionResult:
    cells: list[ZoningPartitionCell] = []
    for index in range(count):
        geometry = box(float(index), 0.0, float(index + 1), 1.0)
        seed = ZoningSeed(
            row=0,
            col=index,
            x_m=index + 0.5,
            y_m=0.5,
            suitability_score=0.9 - 0.1 * index,
        )
        cells.append(
            ZoningPartitionCell(
                seed_index=index,
                seed=seed,
                geometry=geometry,
                area_m2=1.0,
                validity_repaired=index == count - 1,
            )
        )
    return ZoningPartitionResult(
        working_srid=WORKING_SRID,
        developable_area=box(0.0, 0.0, float(count), 1.0),
        cells=tuple(cells),
        developable_area_m2=float(count),
        covered_area_m2=float(count),
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=1 if count else 0,
    )


def _make_assignment(
    partition: ZoningPartitionResult,
    labels: tuple[ZoneClass, ...],
) -> ZoneAssignmentResult:
    assignments = tuple(
        ZoneAssignment(
            cell_index=index,
            seed_index=cell.seed_index,
            zone_class=labels[index],
            area_m2=cell.area_m2,
            suitability_score=cell.seed.suitability_score,
        )
        for index, cell in enumerate(partition.cells)
    )
    total = partition.developable_area_m2
    area_by_class = {
        zone_class: sum(
            item.area_m2 for item in assignments if item.zone_class is zone_class
        )
        for zone_class in ZoneClass
    }
    shares = tuple(
        ZoneShareDiagnostic(
            zone_class=zone_class,
            target_share=area_by_class[zone_class] / total,
            target_area_m2=area_by_class[zone_class],
            assigned_area_m2=area_by_class[zone_class],
            achieved_share=area_by_class[zone_class] / total,
            absolute_area_error_m2=0.0,
            cell_count=sum(item.zone_class is zone_class for item in assignments),
        )
        for zone_class in ZoneClass
    )
    return ZoneAssignmentResult(
        assignments=assignments,
        shares=shares,
        total_area_m2=total,
        zoning_config_version="generated-zone-test-v1",
        zoning_config_fingerprint="b" * 64,
        strategy_version="assignment-test-v1",
    )


def _make_evaluation(
    partition: ZoningPartitionResult,
    assignment: ZoneAssignmentResult,
) -> ZoneConstraintEvaluationResult:
    evaluations: list[EvaluatedZoneConstraints] = []
    for index, (cell, assigned) in enumerate(
        zip(partition.cells, assignment.assignments, strict=True)
    ):
        subject = ZoneConstraintSubject(
            cell_index=index,
            seed_index=cell.seed_index,
            zone_class=assigned.zone_class,
            geometry=cell.geometry,
            area_m2=cell.area_m2,
            suitability_score=assigned.suitability_score,
            working_srid=WORKING_SRID,
        )
        result = ConstraintResult(
            code=f"zone.test-{index}",
            severity=(
                ConstraintSeverity.SOFT if index == 0 else ConstraintSeverity.HARD
            ),
            scope=ConstraintScope.ZONE,
            passed=index == 0,
            message="test zoning constraint result",
        )
        evaluations.append(
            EvaluatedZoneConstraints(
                subject=subject,
                report=ValidationReport(results=(result,)),
            )
        )
    return ZoneConstraintEvaluationResult(
        evaluations=tuple(evaluations),
        stage="zoning",
        evaluator_version="constraint-test-v1",
    )


def _writer() -> SqlAlchemyGeneratedZoneWriter:
    return SqlAlchemyGeneratedZoneWriter(session_factory=SessionLocal, max_insert_rows=1)


def test_writer_persists_zone_class_area_geometry_and_diagnostics(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    partition = _make_partition(2)
    assignment = _make_assignment(
        partition,
        (ZoneClass.RESIDENTIAL, ZoneClass.RECREATION),
    )
    evaluation = _make_evaluation(partition, assignment)

    result = _writer().replace(
        run_id=run.id,
        partition=partition,
        assignment=assignment,
        constraint_evaluation=evaluation,
    )

    assert result.run_id == run.id
    assert result.working_srid == WORKING_SRID
    assert result.deleted_rows == 0
    assert result.inserted_rows == 2
    assert result.insert_statements == 2

    rows = db_session.scalars(
        select(GeneratedZone).where(GeneratedZone.run_id == run.id)
    ).all()
    rows.sort(key=lambda row: row.diagnostics_json["cell_index"])

    assert [row.zone_class for row in rows] == ["residential", "recreation"]
    assert [row.area_m2 for row in rows] == [1.0, 1.0]
    assert all(isinstance(to_shape(row.geometry), MultiPolygon) for row in rows)
    assert rows[0].diagnostics_json["seed_index"] == 0
    assert rows[0].diagnostics_json["constraints"]["is_valid"] is True
    assert rows[1].diagnostics_json["constraints"]["is_valid"] is False
    assert rows[1].diagnostics_json["constraints"]["results"][0] == {
        "code": "zone.test-1",
        "severity": "HARD",
        "scope": "ZONE",
        "passed": False,
        "message": "test zoning constraint result",
    }
    assert rows[1].diagnostics_json["provenance"] == {
        "zoning_config_version": "generated-zone-test-v1",
        "zoning_config_fingerprint": "b" * 64,
        "assignment_strategy_version": "assignment-test-v1",
        "constraint_stage": "zoning",
        "constraint_evaluator_version": "constraint-test-v1",
    }


def test_writer_retry_atomically_replaces_previous_run_zones(db_session: Session) -> None:
    run = _create_run(db_session)
    first_partition = _make_partition(2)
    first_assignment = _make_assignment(
        first_partition,
        (ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
    )
    _writer().replace(
        run_id=run.id,
        partition=first_partition,
        assignment=first_assignment,
        constraint_evaluation=_make_evaluation(first_partition, first_assignment),
    )

    second_partition = _make_partition(1)
    second_assignment = _make_assignment(
        second_partition,
        (ZoneClass.PUBLIC,),
    )
    result = _writer().replace(
        run_id=run.id,
        partition=second_partition,
        assignment=second_assignment,
        constraint_evaluation=_make_evaluation(second_partition, second_assignment),
    )

    persisted = db_session.scalars(
        select(GeneratedZone).where(GeneratedZone.run_id == run.id)
    ).all()
    assert result.deleted_rows == 2
    assert result.inserted_rows == 1
    assert len(persisted) == 1
    assert persisted[0].zone_class == "public"


def test_writer_rejects_successful_run_without_touching_rows(db_session: Session) -> None:
    run = _create_run(db_session, status="succeeded")
    partition = _make_partition(1)
    assignment = _make_assignment(partition, (ZoneClass.RESIDENTIAL,))

    with pytest.raises(GeneratedZoneImmutableError, match="successful"):
        _writer().replace(
            run_id=run.id,
            partition=partition,
            assignment=assignment,
            constraint_evaluation=_make_evaluation(partition, assignment),
        )

    count = db_session.scalar(
        select(func.count()).select_from(GeneratedZone).where(GeneratedZone.run_id == run.id)
    )
    assert count == 0


def test_writer_rejects_partition_run_crs_mismatch(db_session: Session) -> None:
    run = _create_run(db_session)
    partition = _make_partition(1)
    assignment = _make_assignment(partition, (ZoneClass.RESIDENTIAL,))
    evaluation = _make_evaluation(partition, assignment)

    mismatched = ZoningPartitionResult(
        working_srid=32636,
        developable_area=partition.developable_area,
        cells=partition.cells,
        developable_area_m2=partition.developable_area_m2,
        covered_area_m2=partition.covered_area_m2,
        uncovered_area_m2=partition.uncovered_area_m2,
        overlap_area_m2=partition.overlap_area_m2,
        developable_validity_repaired=False,
        repaired_cell_count=partition.repaired_cell_count,
    )

    with pytest.raises(GeneratedZonePersistenceError, match="working_srid"):
        _writer().replace(
            run_id=run.id,
            partition=mismatched,
            assignment=assignment,
            constraint_evaluation=evaluation,
        )


def test_writer_uses_stable_run_scoped_zone_ids(db_session: Session) -> None:
    run = _create_run(db_session)
    partition = _make_partition(2)
    assignment = _make_assignment(
        partition,
        (ZoneClass.RESIDENTIAL, ZoneClass.RECREATION),
    )
    evaluation = _make_evaluation(partition, assignment)

    _writer().replace(
        run_id=run.id,
        partition=partition,
        assignment=assignment,
        constraint_evaluation=evaluation,
    )
    first_ids = tuple(
        sorted(
            str(value)
            for value in db_session.scalars(
                select(GeneratedZone.id).where(GeneratedZone.run_id == run.id)
            ).all()
        )
    )

    _writer().replace(
        run_id=run.id,
        partition=partition,
        assignment=assignment,
        constraint_evaluation=evaluation,
    )
    second_ids = tuple(
        sorted(
            str(value)
            for value in db_session.scalars(
                select(GeneratedZone.id).where(GeneratedZone.run_id == run.id)
            ).all()
        )
    )

    assert second_ids == first_ids
