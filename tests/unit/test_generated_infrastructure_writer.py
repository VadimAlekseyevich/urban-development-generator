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
    MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES,
    InfrastructureAcceptedFacility,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryDiagnostics,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateGeometryResult,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateSource,
    InfrastructureCandidateUnsnapped,
    InfrastructureCategory,
    InfrastructureCoverageCacheEntry,
    InfrastructureDemandModel,
    InfrastructureGreedyPlacementState,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapDiagnostics,
    InfrastructureNetworkUnsnappedReason,
    InfrastructureType,
)
from core.urban_generator.zoning import ZoneClass

TYPE_CODE = "school.general"
SNAPSHOT_ID = "roads:writer-v1"
WORKING_SRID = 32637


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
    geometry = box(0.0, 0.0, 100.0, 2.0)
    return InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id=f"parcel:{candidate_id}",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(1.0, 1.0),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=geometry,
        site_area_m2=float(geometry.area),
        zone_id="zone:a",
        block_id="block:a",
    )


def _host(candidate_id: str, building_id: str) -> InfrastructureCandidateGeometry:
    return InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.BUILDING,
        source_id=building_id,
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(50.0, 50.0),
        kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
        host_building_id=building_id,
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
    candidate_refs = tuple(_ref(item) for item in candidate_ids)
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


def _snaps(
    candidate_ids: tuple[str, ...],
    *,
    unsnapped_ids: tuple[str, ...] = (),
) -> InfrastructureNetworkSnapBatchResult:
    unsnapped_set = set(unsnapped_ids)
    snapped = tuple(
        InfrastructureCandidateSnap(
            ref=_ref(candidate_id),
            node=NetworkNodeRef(node_id=f"node:{candidate_id}"),
            distance_m=float(index + 1),
        )
        for index, candidate_id in enumerate(candidate_ids)
        if candidate_id not in unsnapped_set
    )
    unsnapped = tuple(
        InfrastructureCandidateUnsnapped(
            ref=_ref(candidate_id),
            reason=(
                InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE
            ),
        )
        for candidate_id in candidate_ids
        if candidate_id in unsnapped_set
    )
    return InfrastructureNetworkSnapBatchResult(
        snapshot_id=SNAPSHOT_ID,
        working_srid=WORKING_SRID,
        snapped=snapped,
        unsnapped=unsnapped,
        diagnostics=InfrastructureNetworkSnapDiagnostics(
            input_count=len(candidate_ids),
            snapped_count=len(snapped),
            unsnapped_count=len(unsnapped),
            empty_network_count=0,
            no_node_within_max_distance_count=len(unsnapped),
        ),
    )


def test_writer_alignment_requires_accepted_candidate_network_snap() -> None:
    state = _state(("candidate-a",), accepted_ids=("candidate-a",))

    with pytest.raises(
        GeneratedInfrastructurePersistenceError,
        match="must have a successful network snap",
    ):
        SqlAlchemyGeneratedInfrastructureWriter._validate_alignment(
            working_srid=WORKING_SRID,
            state=state,
            candidate_geometry=_geometry_result((_site("candidate-a"),)),
            candidate_snaps=_snaps(
                ("candidate-a",),
                unsnapped_ids=("candidate-a",),
            ),
            infrastructure_type=_type(),
        )


def test_writer_alignment_requires_exact_candidate_geometry_coverage() -> None:
    state = _state(
        ("candidate-a", "candidate-b"),
        accepted_ids=("candidate-a",),
    )

    with pytest.raises(
        GeneratedInfrastructurePersistenceError,
        match="cover exactly placement candidate_order",
    ):
        SqlAlchemyGeneratedInfrastructureWriter._validate_alignment(
            working_srid=WORKING_SRID,
            state=state,
            candidate_geometry=_geometry_result((_site("candidate-a"),)),
            candidate_snaps=_snaps(("candidate-a", "candidate-b")),
            infrastructure_type=_type(),
        )


def test_writer_rows_preserve_site_host_network_and_deterministic_identity() -> None:
    infrastructure_type = _type()
    site = _site("candidate-a")
    host = _host("candidate-b", "building:b")
    state = _state(
        ("candidate-a", "candidate-b"),
        accepted_ids=("candidate-a", "candidate-b"),
    )
    geometry_by_key, snap_by_key = (
        SqlAlchemyGeneratedInfrastructureWriter._validate_alignment(
            working_srid=WORKING_SRID,
            state=state,
            candidate_geometry=_geometry_result((site, host)),
            candidate_snaps=_snaps(("candidate-a", "candidate-b")),
            infrastructure_type=infrastructure_type,
        )
    )
    run_id = uuid.uuid4()
    host_db_id = uuid.uuid4()

    rows = SqlAlchemyGeneratedInfrastructureWriter._rows_from_results(
        run_id=run_id,
        working_srid=WORKING_SRID,
        state=state,
        geometry_by_key=geometry_by_key,
        snap_by_key=snap_by_key,
        host_buildings={"building:b": host_db_id},
        infrastructure_type=infrastructure_type,
        network_snapshot_id=SNAPSHOT_ID,
    )

    assert len(rows) == 2
    site_row, host_row = rows

    assert site_row["id"] == uuid.uuid5(
        run_id,
        f"generated-infrastructure:{TYPE_CODE}:candidate-a",
    )
    assert site_row["geometry_kind"] == "site"
    assert site_row["host_building_id"] is None
    assert site_row["site_area_m2"] == pytest.approx(200.0)
    assert to_shape(site_row["geometry"]).equals(site.site_geometry)
    assert site_row["network_snapshot_id"] == SNAPSHOT_ID
    assert site_row["network_node_id"] == "node:candidate-a"
    assert site_row["capacity"] == pytest.approx(600.0)
    assert site_row["category"] == "education"

    assert host_row["geometry_kind"] == "host_building"
    assert host_row["host_building_id"] == host_db_id
    assert host_row["site_area_m2"] is None
    assert to_shape(host_row["geometry"]).equals(host.anchor)
    assert host_row["network_node_id"] == "node:candidate-b"
    assert host_row["attributes_json"]["source_id"] == "building:b"


def test_writer_rejects_row_bound_above_placement_hard_limit() -> None:
    with pytest.raises(ValueError, match="max_rows exceeds"):
        SqlAlchemyGeneratedInfrastructureWriter(
            max_rows=MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES + 1
        )
