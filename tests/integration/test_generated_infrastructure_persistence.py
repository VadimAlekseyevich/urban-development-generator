import uuid

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point, box
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.generated_infrastructure_writer import (
    GeneratedInfrastructureImmutableError,
    GeneratedInfrastructurePersistenceError,
    SqlAlchemyGeneratedInfrastructureWriter,
)
from backend.app.db.session import SessionLocal, engine
from backend.app.models.generated_entity import (
    GeneratedBuilding,
    GeneratedInfrastructure,
)
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import NetworkNodeRef
from core.urban_generator.infrastructure import (
    InfrastructureAcceptedFacility,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryDiagnostics,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateGeometryResult,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureCoverageCacheEntry,
    InfrastructureDemandModel,
    InfrastructureGreedyPlacementState,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapDiagnostics,
    InfrastructureType,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 32637
SNAPSHOT_ID = "roads:persistence-v1"
TYPE_CODE = "school.general"


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
        name="Generated infrastructure persistence",
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


def _create_building(
    session: Session,
    *,
    run_id: uuid.UUID,
    building_key: str,
) -> GeneratedBuilding:
    building = GeneratedBuilding(
        id=uuid.uuid5(run_id, f"generated-building:{building_key}"),
        run_id=run_id,
        geometry=from_shape(
            box(40.0, 40.0, 60.0, 60.0),
            srid=WORKING_SRID,
        ),
        building_key=building_key,
        source_id="block:host",
        zone_class="public",
        archetype="public",
        building_use="public",
        floors=2,
        footprint_area_m2=400.0,
        gfa_m2=800.0,
        attributes_json={},
    )
    session.add(building)
    session.commit()
    return building


def _type() -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=1.0,
        ),
        capacity=600.0,
        max_network_distance_m=1_500.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=100.0,
        target_site_area_m2=200.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(
                InfrastructureCandidateSource.PARCEL,
                InfrastructureCandidateSource.BUILDING,
            )
        ),
    )


def _ref(candidate_id: str) -> InfrastructureCandidateRef:
    return InfrastructureCandidateRef(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _site(candidate_id: str) -> InfrastructureCandidateGeometry:
    geometry = box(0.0, 0.0, 10.0, 10.0)
    return InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id=f"parcel:{candidate_id}",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(5.0, 5.0),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=geometry,
        site_area_m2=float(geometry.area),
        zone_id="zone:a",
        block_id="block:a",
    )


def _host(candidate_id: str, building_key: str) -> InfrastructureCandidateGeometry:
    return InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.BUILDING,
        source_id=building_key,
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(50.0, 50.0),
        kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
        host_building_id=building_key,
    )


def _geometry_result(
    candidates: tuple[InfrastructureCandidateGeometry, ...],
) -> InfrastructureCandidateGeometryResult:
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    host_count = sum(
        item.kind is InfrastructureCandidateGeometryKind.HOST_BUILDING
        for item in ordered
    )
    return InfrastructureCandidateGeometryResult(
        infrastructure_type_code=TYPE_CODE,
        working_srid=WORKING_SRID,
        candidates=ordered,
        diagnostics=InfrastructureCandidateGeometryDiagnostics(
            candidate_count=len(ordered),
            polygon_site_count=len(ordered) - host_count,
            host_building_count=host_count,
            clipped_site_count=0,
            full_source_site_count=len(ordered) - host_count,
        ),
    )


def _state(
    candidate_ids: tuple[str, ...],
    *,
    accepted_ids: tuple[str, ...],
) -> InfrastructureGreedyPlacementState:
    ordered_ids = tuple(sorted(candidate_ids))
    candidate_refs = tuple(_ref(item) for item in ordered_ids)
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(),
        accepted_facilities=tuple(
            InfrastructureAcceptedFacility(
                candidate_ref=_ref(candidate_id),
                acceptance_index=index,
            )
            for index, candidate_id in enumerate(accepted_ids)
        ),
        coverage_cache=tuple(
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                candidate_ref=ref,
                accessibility=(),
            )
            for ref in candidate_refs
        ),
        candidate_order=candidate_refs,
    )


def _snaps(candidate_ids: tuple[str, ...]) -> InfrastructureNetworkSnapBatchResult:
    ordered_ids = tuple(sorted(candidate_ids))
    snapped = tuple(
        InfrastructureCandidateSnap(
            ref=_ref(candidate_id),
            node=NetworkNodeRef(node_id=f"node:{candidate_id}"),
            distance_m=float(index + 1),
        )
        for index, candidate_id in enumerate(ordered_ids)
    )
    return InfrastructureNetworkSnapBatchResult(
        snapshot_id=SNAPSHOT_ID,
        working_srid=WORKING_SRID,
        snapped=snapped,
        unsnapped=(),
        diagnostics=InfrastructureNetworkSnapDiagnostics(
            input_count=len(snapped),
            snapped_count=len(snapped),
            unsnapped_count=0,
            empty_network_count=0,
            no_node_within_max_distance_count=0,
        ),
    )


def _writer() -> SqlAlchemyGeneratedInfrastructureWriter:
    return SqlAlchemyGeneratedInfrastructureWriter(
        session_factory=SessionLocal,
        max_insert_rows=1,
    )


def test_writer_persists_site_host_capacity_and_network_provenance(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    building = _create_building(
        db_session,
        run_id=run.id,
        building_key="building:host",
    )
    state = _state(
        ("candidate-a", "candidate-b"),
        accepted_ids=("candidate-a", "candidate-b"),
    )
    geometry = _geometry_result(
        (
            _site("candidate-a"),
            _host("candidate-b", "building:host"),
        )
    )

    result = _writer().replace(
        run_id=run.id,
        state=state,
        candidate_geometry=geometry,
        candidate_snaps=_snaps(("candidate-a", "candidate-b")),
        infrastructure_type=_type(),
    )

    assert result.deleted_rows == 0
    assert result.inserted_rows == 2
    assert result.insert_statements == 2
    assert result.host_building_ref_count == 1

    db_session.expire_all()
    rows = db_session.scalars(
        select(GeneratedInfrastructure)
        .where(GeneratedInfrastructure.run_id == run.id)
        .order_by(GeneratedInfrastructure.acceptance_index)
    ).all()
    assert len(rows) == 2

    site_row, host_row = rows
    assert site_row.id == uuid.uuid5(
        run.id,
        f"generated-infrastructure:{TYPE_CODE}:candidate-a",
    )
    assert site_row.category == "education"
    assert site_row.capacity == pytest.approx(600.0)
    assert site_row.geometry_kind == "site"
    assert site_row.site_area_m2 == pytest.approx(100.0)
    assert site_row.host_building_id is None
    assert to_shape(site_row.geometry).equals(box(0.0, 0.0, 10.0, 10.0))
    assert site_row.network_snapshot_id == SNAPSHOT_ID
    assert site_row.network_node_id == "node:candidate-a"
    assert site_row.network_snap_distance_m == pytest.approx(1.0)

    assert host_row.geometry_kind == "host_building"
    assert host_row.host_building_id == building.id
    assert host_row.site_area_m2 is None
    assert to_shape(host_row.geometry).equals(Point(50.0, 50.0))
    assert host_row.network_node_id == "node:candidate-b"
    assert host_row.attributes_json["source_id"] == "building:host"
    assert (
        host_row.attributes_json["infrastructure_type_fingerprint"]
        == _type().fingerprint
    )


def test_retry_replaces_only_target_type_and_preserves_identity(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    state = _state(("candidate-a",), accepted_ids=("candidate-a",))
    geometry = _geometry_result((_site("candidate-a"),))
    snaps = _snaps(("candidate-a",))
    writer = _writer()

    writer.replace(
        run_id=run.id,
        state=state,
        candidate_geometry=geometry,
        candidate_snaps=snaps,
        infrastructure_type=_type(),
    )
    db_session.expire_all()
    first_id = db_session.scalar(
        select(GeneratedInfrastructure.id).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code == TYPE_CODE,
        )
    )

    other = GeneratedInfrastructure(
        id=uuid.uuid4(),
        run_id=run.id,
        geometry=from_shape(box(20.0, 0.0, 30.0, 10.0), srid=WORKING_SRID),
        candidate_id="clinic.primary:parcel:p1",
        infrastructure_type_code="clinic.primary",
        category="healthcare",
        capacity=100.0,
        acceptance_index=0,
        geometry_kind="site",
        site_area_m2=100.0,
        network_snapshot_id=SNAPSHOT_ID,
        network_node_id="node:clinic",
        network_snap_distance_m=2.0,
        attributes_json={},
    )
    db_session.add(other)
    db_session.commit()

    result = writer.replace(
        run_id=run.id,
        state=state,
        candidate_geometry=geometry,
        candidate_snaps=snaps,
        infrastructure_type=_type(),
    )
    db_session.expire_all()

    second_id = db_session.scalar(
        select(GeneratedInfrastructure.id).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code == TYPE_CODE,
        )
    )
    other_count = db_session.scalar(
        select(func.count())
        .select_from(GeneratedInfrastructure)
        .where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code == "clinic.primary",
        )
    )

    assert result.deleted_rows == 1
    assert result.inserted_rows == 1
    assert first_id == second_id
    assert other_count == 1


def test_invalid_host_ref_fails_before_existing_type_rows_are_deleted(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    writer = _writer()
    writer.replace(
        run_id=run.id,
        state=_state(("candidate-a",), accepted_ids=("candidate-a",)),
        candidate_geometry=_geometry_result((_site("candidate-a"),)),
        candidate_snaps=_snaps(("candidate-a",)),
        infrastructure_type=_type(),
    )

    with pytest.raises(
        GeneratedInfrastructurePersistenceError,
        match="host building refs must resolve",
    ):
        writer.replace(
            run_id=run.id,
            state=_state(("candidate-b",), accepted_ids=("candidate-b",)),
            candidate_geometry=_geometry_result(
                (_host("candidate-b", "building:missing"),)
            ),
            candidate_snaps=_snaps(("candidate-b",)),
            infrastructure_type=_type(),
        )

    db_session.expire_all()
    persisted = db_session.scalars(
        select(GeneratedInfrastructure).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code == TYPE_CODE,
        )
    ).all()
    assert len(persisted) == 1
    assert persisted[0].candidate_id == "candidate-a"


def test_writer_rejects_successful_run_without_persisting_rows(
    db_session: Session,
) -> None:
    run = _create_run(db_session, status="succeeded")

    with pytest.raises(
        GeneratedInfrastructureImmutableError,
        match="successful generation run",
    ):
        _writer().replace(
            run_id=run.id,
            state=_state(("candidate-a",), accepted_ids=("candidate-a",)),
            candidate_geometry=_geometry_result((_site("candidate-a"),)),
            candidate_snaps=_snaps(("candidate-a",)),
            infrastructure_type=_type(),
        )

    assert db_session.scalar(
        select(func.count())
        .select_from(GeneratedInfrastructure)
        .where(GeneratedInfrastructure.run_id == run.id)
    ) == 0


def test_database_rejects_partial_typed_infrastructure_shape(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    db_session.add(
        GeneratedInfrastructure(
            run_id=run.id,
            geometry=from_shape(box(0.0, 0.0, 10.0, 10.0), srid=WORKING_SRID),
            candidate_id="candidate-partial",
            attributes_json={},
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_generated_infrastructure_required_indexes_exist(
    db_session: Session,
) -> None:
    indexes = set(
        db_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND tablename = 'generated_infrastructure'"
            )
        ).all()
    )

    assert {
        "ix_generated_infrastructure_run_id_id",
        "ix_generated_infrastructure_geometry",
        "uq_generated_infrastructure_run_candidate_type",
        "uq_generated_infrastructure_run_type_acceptance",
    } <= indexes
