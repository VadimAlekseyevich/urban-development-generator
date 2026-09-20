import uuid

import pytest
from geoalchemy2.shape import to_shape
from shapely.geometry import Point, box

from backend.app.db.generated_infrastructure_writer import (
    GeneratedInfrastructurePersistenceError,
    SqlAlchemyGeneratedInfrastructureWriter,
)
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
    InfrastructureFeasibilityResult,
    InfrastructureGreedyPlacementState,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapDiagnostics,
    InfrastructureType,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857
SNAPSHOT_ID = "network:test-v1"
TYPE_CODE = "school.general"


def _type() -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.TOTAL_POPULATION,
            demand_rate=0.01,
        ),
        capacity=100.0,
        max_network_distance_m=1500.0,
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


def _candidates() -> InfrastructureCandidateGeometryResult:
    site = box(0.0, 0.0, 20.0, 10.0)
    values = (
        InfrastructureCandidateGeometry(
            candidate_id="candidate-a",
            infrastructure_type_code=TYPE_CODE,
            source_kind=InfrastructureCandidateSource.PARCEL,
            source_id="parcel-a",
            zone_class=ZoneClass.PUBLIC,
            working_srid=WORKING_SRID,
            anchor=Point(10.0, 5.0),
            kind=InfrastructureCandidateGeometryKind.SITE,
            site_geometry=site,
            site_area_m2=float(site.area),
            zone_id="zone-a",
            block_id="block-a",
        ),
        InfrastructureCandidateGeometry(
            candidate_id="candidate-b",
            infrastructure_type_code=TYPE_CODE,
            source_kind=InfrastructureCandidateSource.BUILDING,
            source_id="building-b",
            zone_class=ZoneClass.PUBLIC,
            working_srid=WORKING_SRID,
            anchor=Point(40.0, 5.0),
            kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
            host_building_id="building-b",
        ),
    )
    return InfrastructureCandidateGeometryResult(
        infrastructure_type_code=TYPE_CODE,
        working_srid=WORKING_SRID,
        candidates=values,
        diagnostics=InfrastructureCandidateGeometryDiagnostics(
            candidate_count=2,
            polygon_site_count=1,
            host_building_count=1,
            clipped_site_count=1,
            full_source_site_count=0,
        ),
    )


def _refs() -> tuple[InfrastructureCandidateRef, ...]:
    return (
        InfrastructureCandidateRef(
            candidate_id="candidate-a",
            infrastructure_type_code=TYPE_CODE,
        ),
        InfrastructureCandidateRef(
            candidate_id="candidate-b",
            infrastructure_type_code=TYPE_CODE,
        ),
    )


def _placement() -> InfrastructureGreedyPlacementState:
    refs = _refs()
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(),
        accepted_facilities=(
            InfrastructureAcceptedFacility(
                candidate_ref=refs[0],
                acceptance_index=0,
            ),
            InfrastructureAcceptedFacility(
                candidate_ref=refs[1],
                acceptance_index=1,
            ),
        ),
        coverage_cache=tuple(
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                candidate_ref=ref,
                accessibility=(),
            )
            for ref in refs
        ),
        candidate_order=refs,
    )


def _feasibility() -> tuple[InfrastructureFeasibilityResult, ...]:
    return (
        InfrastructureFeasibilityResult(
            candidate_id="candidate-a",
            infrastructure_type_code=TYPE_CODE,
            working_srid=WORKING_SRID,
            geometry_kind=InfrastructureCandidateGeometryKind.SITE,
            proposed_capacity=100.0,
            is_feasible=True,
        ),
        InfrastructureFeasibilityResult(
            candidate_id="candidate-b",
            infrastructure_type_code=TYPE_CODE,
            working_srid=WORKING_SRID,
            geometry_kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
            proposed_capacity=100.0,
            is_feasible=True,
        ),
    )


def _snaps(
    *,
    include_host: bool = True,
) -> InfrastructureNetworkSnapBatchResult:
    refs = _refs()
    snapped = [
        InfrastructureCandidateSnap(
            ref=refs[0],
            node=NetworkNodeRef("node-a"),
            distance_m=1.5,
        )
    ]
    if include_host:
        snapped.append(
            InfrastructureCandidateSnap(
                ref=refs[1],
                node=NetworkNodeRef("node-b"),
                distance_m=2.5,
            )
        )
    return InfrastructureNetworkSnapBatchResult(
        snapshot_id=SNAPSHOT_ID,
        working_srid=WORKING_SRID,
        snapped=tuple(snapped),
        unsnapped=(),
        diagnostics=InfrastructureNetworkSnapDiagnostics(
            input_count=len(snapped),
            snapped_count=len(snapped),
            unsnapped_count=0,
            empty_network_count=0,
            no_node_within_max_distance_count=0,
        ),
    )


def _writer(
    *,
    max_rows: int = 10,
) -> SqlAlchemyGeneratedInfrastructureWriter:
    return SqlAlchemyGeneratedInfrastructureWriter(
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        max_rows=max_rows,
        max_insert_rows=2,
    )


def test_generated_infrastructure_identity_is_deterministic_and_scoped() -> None:
    run_id = uuid.uuid4()

    first = SqlAlchemyGeneratedInfrastructureWriter._stable_entity_id(
        run_id,
        TYPE_CODE,
        "candidate-a",
    )
    second = SqlAlchemyGeneratedInfrastructureWriter._stable_entity_id(
        run_id,
        TYPE_CODE,
        "candidate-a",
    )
    other = SqlAlchemyGeneratedInfrastructureWriter._stable_entity_id(
        run_id,
        TYPE_CODE,
        "candidate-b",
    )

    assert first == second
    assert first != other


def test_writer_aligns_authoritative_t05_t06_t08_t09_results() -> None:
    writer = _writer()

    subjects = writer._validate_alignment(
        working_srid=WORKING_SRID,
        infrastructure_type=_type(),
        candidates=_candidates(),
        placement=_placement(),
        feasibility_results=_feasibility(),
        network_snaps=_snaps(),
    )

    assert [item.candidate.candidate_id for item in subjects] == [
        "candidate-a",
        "candidate-b",
    ]
    assert [item.snap.node.node_id for item in subjects] == [
        "node-a",
        "node-b",
    ]


def test_writer_rejects_accepted_candidate_without_successful_network_snap() -> None:
    with pytest.raises(
        GeneratedInfrastructurePersistenceError,
        match="successful T06 candidate network snap",
    ):
        _writer()._validate_alignment(
            working_srid=WORKING_SRID,
            infrastructure_type=_type(),
            candidates=_candidates(),
            placement=_placement(),
            feasibility_results=_feasibility(),
            network_snaps=_snaps(include_host=False),
        )


def test_writer_enforces_explicit_row_bound_before_persistence() -> None:
    with pytest.raises(
        GeneratedInfrastructurePersistenceError,
        match="row limit exceeded",
    ):
        _writer(max_rows=1)._validate_alignment(
            working_srid=WORKING_SRID,
            infrastructure_type=_type(),
            candidates=_candidates(),
            placement=_placement(),
            feasibility_results=_feasibility(),
            network_snaps=_snaps(),
        )


def test_writer_materializes_site_and_host_rows_without_recomputation() -> None:
    writer = _writer()
    infrastructure_type = _type()
    subjects = writer._validate_alignment(
        working_srid=WORKING_SRID,
        infrastructure_type=infrastructure_type,
        candidates=_candidates(),
        placement=_placement(),
        feasibility_results=_feasibility(),
        network_snaps=_snaps(),
    )
    host_id = uuid.uuid4()

    rows = writer._rows_from_subjects(
        run_id=uuid.uuid4(),
        working_srid=WORKING_SRID,
        infrastructure_type=infrastructure_type,
        snapshot_id=SNAPSHOT_ID,
        subjects=subjects,
        host_refs={"building-b": host_id},
    )

    site_row, host_row = rows
    assert site_row["capacity"] == 100.0
    assert site_row["acceptance_index"] == 0
    assert site_row["geometry_kind"] == "site"
    assert site_row["site_area_m2"] == 200.0
    assert site_row["host_building_id"] is None
    assert to_shape(site_row["geometry"]).equals(box(0.0, 0.0, 20.0, 10.0))
    assert site_row["network_node_id"] == "node-a"

    assert host_row["capacity"] == 100.0
    assert host_row["acceptance_index"] == 1
    assert host_row["geometry_kind"] == "host_building"
    assert host_row["site_area_m2"] is None
    assert host_row["host_building_id"] == host_id
    assert to_shape(host_row["geometry"]).equals(Point(40.0, 5.0))
    assert host_row["network_node_id"] == "node-b"
