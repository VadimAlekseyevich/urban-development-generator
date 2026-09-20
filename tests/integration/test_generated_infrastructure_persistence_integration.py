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


def _create_run(
    session: Session,
    *,
    status: str = "running",
) -> GenerationRun:
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


def _create_generated_building(
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
        attributes_json={},
    )
    session.add(building)
    session.commit()
    return building


def _type(
    code: str,
    *,
    category: InfrastructureCategory = InfrastructureCategory.EDUCATION,
    capacity: float = 600.0,
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=code,
        category=category,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=1.0,
        ),
        capacity=capacity,
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


def _site(
    type_code: str,
    candidate_id: str,
    *,
    x_offset: float = 0.0,
) -> InfrastructureCandidateGeometry:
    geometry = box(x_offset, 0.0, x_offset + 20.0, 10.0)
    return InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code=type_code,
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id=f"parcel:{candidate_id}",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(x_offset + 10.0, 5.0),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=geometry,
        site_area_m2=float(geometry.area),
        zone_id="zone:a",
        block_id="block:a",
    )


def _host(
    type_code: str,
    candidate_id: str,
    *,
    building_key: str,
) -> InfrastructureCandidateGeometry:
    return InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code=type_code,
        source_kind=InfrastructureCandidateSource.BUILDING,
        source_id=building_key,
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(50.0, 50.0),
        kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
        host_building_id=building_key,
    )


def _geometry_result(
    type_code: str,
    candidates: tuple[InfrastructureCandidateGeometry, ...],
) -> InfrastructureCandidateGeometryResult:
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    host_count = sum(
        item.kind is InfrastructureCandidateGeometryKind.HOST_BUILDING
        for item in ordered
    )
    return InfrastructureCandidateGeometryResult(
        infrastructure_type_code=type_code,
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


def _ref(type_code: str, candidate_id: str) -> InfrastructureCandidateRef:
    return InfrastructureCandidateRef(
        candidate_id=candidate_id,
        infrastructure_type_code=type_code,
    )


def _state(
    type_code: str,
    candidate_ids: tuple[str, ...],
    *,
    accepted_ids: tuple[str, ...],
) -> InfrastructureGreedyPlacementState:
    refs = tuple(_ref(type_code, item) for item in candidate_ids)
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=type_code,
        remaining_demand=(),
        accepted_facilities=tuple(
            InfrastructureAcceptedFacility(
                candidate_ref=_ref(type_code, candidate_id),
                acceptance_index=index,
            )
            for index, candidate_id in enumerate(accepted_ids)
        ),
        coverage_cache=tuple(
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=type_code,
                candidate_ref=ref,
                accessibility=(),
            )
            for ref in refs
        ),
        candidate_order=refs,
    )


def _snaps(
    type_code: str,
    candidate_ids: tuple[str, ...],
) -> InfrastructureNetworkSnapBatchResult:
    snapped = tuple(
        InfrastructureCandidateSnap(
            ref=_ref(type_code, candidate_id),
            node=NetworkNodeRef(node_id=f"node:{candidate_id}"),
            distance_m=float(index + 1),
        )
        for index, candidate_id in enumerate(candidate_ids)
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


def _replace(
    *,
    run_id: uuid.UUID,
    infrastructure_type: InfrastructureType,
    candidates: tuple[InfrastructureCandidateGeometry, ...],
    accepted_ids: tuple[str, ...],
):
    candidate_ids = tuple(
        item.candidate_id
        for item in sorted(candidates, key=lambda item: item.candidate_id)
    )
    return _writer().replace(
        run_id=run_id,
        state=_state(
            infrastructure_type.code,
            candidate_ids,
            accepted_ids=accepted_ids,
        ),
        candidate_geometry=_geometry_result(
            infrastructure_type.code,
            candidates,
        ),
        candidate_snaps=_snaps(infrastructure_type.code, candidate_ids),
        infrastructure_type=infrastructure_type,
    )


def test_writer_persists_site_host_capacity_and_network_provenance(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    host_building = _create_generated_building(
        db_session,
        run_id=run.id,
        building_key="building:b",
    )
    infrastructure_type = _type("school.general")
    site = _site(infrastructure_type.code, "candidate-a")
    host = _host(
        infrastructure_type.code,
        "candidate-b",
        building_key="building:b",
    )

    result = _replace(
        run_id=run.id,
        infrastructure_type=infrastructure_type,
        candidates=(site, host),
        accepted_ids=("candidate-a", "candidate-b"),
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
        "generated-infrastructure:school.general:candidate-a",
    )
    assert site_row.candidate_id == "candidate-a"
    assert site_row.infrastructure_type_code == "school.general"
    assert site_row.category == "education"
    assert site_row.capacity == pytest.approx(600.0)
    assert site_row.acceptance_index == 0
    assert site_row.geometry_kind == "site"
    assert site_row.host_building_id is None
    assert site_row.site_area_m2 == pytest.approx(200.0)
    assert site_row.network_snapshot_id == SNAPSHOT_ID
    assert site_row.network_node_id == "node:candidate-a"
    assert site_row.network_snap_distance_m == pytest.approx(1.0)
    assert to_shape(site_row.geometry).equals(site.site_geometry)

    assert host_row.candidate_id == "candidate-b"
    assert host_row.acceptance_index == 1
    assert host_row.geometry_kind == "host_building"
    assert host_row.host_building_id == host_building.id
    assert host_row.site_area_m2 is None
    assert host_row.network_node_id == "node:candidate-b"
    assert host_row.network_snap_distance_m == pytest.approx(2.0)
    assert to_shape(host_row.geometry).equals(host.anchor)
    assert host_row.attributes_json["source_id"] == "building:b"
    assert host_row.attributes_json["infrastructure_type_version"] == (
        "infrastructure-v1"
    )


def test_retry_replaces_only_target_type_and_preserves_deterministic_id(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    school = _type("school.general")
    clinic = _type(
        "clinic.general",
        category=InfrastructureCategory.HEALTHCARE,
        capacity=300.0,
    )
    school_site = _site(school.code, "candidate-a")
    clinic_site = _site(clinic.code, "candidate-a", x_offset=100.0)

    _replace(
        run_id=run.id,
        infrastructure_type=school,
        candidates=(school_site,),
        accepted_ids=("candidate-a",),
    )
    _replace(
        run_id=run.id,
        infrastructure_type=clinic,
        candidates=(clinic_site,),
        accepted_ids=("candidate-a",),
    )
    db_session.expire_all()
    school_id = db_session.scalar(
        select(GeneratedInfrastructure.id).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code == school.code,
        )
    )
    clinic_id = db_session.scalar(
        select(GeneratedInfrastructure.id).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code == clinic.code,
        )
    )

    result = _replace(
        run_id=run.id,
        infrastructure_type=school,
        candidates=(school_site,),
        accepted_ids=("candidate-a",),
    )
    db_session.expire_all()

    assert result.deleted_rows == 1
    assert result.inserted_rows == 1
    assert db_session.scalar(
        select(GeneratedInfrastructure.id).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code == school.code,
        )
    ) == school_id
    assert db_session.scalar(
        select(GeneratedInfrastructure.id).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code == clinic.code,
        )
    ) == clinic_id
    assert db_session.scalar(
        select(func.count())
        .select_from(GeneratedInfrastructure)
        .where(GeneratedInfrastructure.run_id == run.id)
    ) == 2


def test_missing_host_fails_before_existing_type_rows_are_deleted(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    infrastructure_type = _type("school.general")
    site = _site(infrastructure_type.code, "candidate-a")
    _replace(
        run_id=run.id,
        infrastructure_type=infrastructure_type,
        candidates=(site,),
        accepted_ids=("candidate-a",),
    )
    db_session.expire_all()
    original_id = db_session.scalar(
        select(GeneratedInfrastructure.id).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code
            == infrastructure_type.code,
        )
    )

    missing_host = _host(
        infrastructure_type.code,
        "candidate-b",
        building_key="building:missing",
    )
    with pytest.raises(
        GeneratedInfrastructurePersistenceError,
        match="host building refs must resolve",
    ):
        _replace(
            run_id=run.id,
            infrastructure_type=infrastructure_type,
            candidates=(missing_host,),
            accepted_ids=("candidate-b",),
        )

    db_session.expire_all()
    assert db_session.scalar(
        select(GeneratedInfrastructure.id).where(
            GeneratedInfrastructure.run_id == run.id,
            GeneratedInfrastructure.infrastructure_type_code
            == infrastructure_type.code,
        )
    ) == original_id


def test_writer_rejects_successful_run_without_touching_rows(
    db_session: Session,
) -> None:
    run = _create_run(db_session, status="succeeded")
    infrastructure_type = _type("school.general")
    site = _site(infrastructure_type.code, "candidate-a")

    with pytest.raises(
        GeneratedInfrastructureImmutableError,
        match="successful generation run",
    ):
        _replace(
            run_id=run.id,
            infrastructure_type=infrastructure_type,
            candidates=(site,),
            accepted_ids=("candidate-a",),
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
    row = GeneratedInfrastructure(
        run_id=run.id,
        geometry=from_shape(
            box(0.0, 0.0, 20.0, 10.0),
            srid=WORKING_SRID,
        ),
        candidate_id="candidate-partial",
        attributes_json={},
    )
    db_session.add(row)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_database_rejects_geometry_kind_mismatch(
    db_session: Session,
) -> None:
    run = _create_run(db_session)
    row = GeneratedInfrastructure(
        run_id=run.id,
        geometry=from_shape(Point(0.0, 0.0), srid=WORKING_SRID),
        candidate_id="candidate-site",
        infrastructure_type_code="school.general",
        category="education",
        capacity=600.0,
        acceptance_index=0,
        geometry_kind="site",
        site_area_m2=200.0,
        network_snapshot_id=SNAPSHOT_ID,
        network_node_id="node:a",
        network_snap_distance_m=0.0,
        attributes_json={},
    )
    db_session.add(row)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_generated_infrastructure_required_indexes_exist(
    db_session: Session,
) -> None:
    definitions = {
        name: definition
        for name, definition in db_session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND tablename = 'generated_infrastructure'"
            )
        ).all()
    }

    assert "ix_generated_infrastructure_run_id_id" in definitions
    assert "ix_generated_infrastructure_geometry" in definitions
    assert "uq_generated_infrastructure_run_candidate_type" in definitions
    assert "uq_generated_infrastructure_run_type_acceptance" in definitions
    assert "USING gist (geometry)" in definitions[
        "ix_generated_infrastructure_geometry"
    ]
    assert "(run_id, infrastructure_type_code, acceptance_index)" in definitions[
        "uq_generated_infrastructure_run_type_acceptance"
    ]
