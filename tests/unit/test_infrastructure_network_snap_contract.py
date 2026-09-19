from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import (
    NetworkGraphSnapshot,
    NetworkNodeRef,
    NetworkPoint,
    WorkingCRS,
)
from core.urban_generator.infrastructure import (
    MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE,
    BlockInfrastructureDemand,
    ExistingInfrastructureFacility,
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnap,
    ExistingInfrastructureFacilitySnapInput,
    ExistingInfrastructureFacilityUnsnapped,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateSnapInput,
    InfrastructureCandidateSource,
    InfrastructureCandidateUnsnapped,
    InfrastructureCategory,
    InfrastructureDemandRef,
    InfrastructureDemandSnap,
    InfrastructureDemandSnapInput,
    InfrastructureDemandUnsnapped,
    InfrastructureNetworkSnapDiagnostics,
    InfrastructureNetworkSnapError,
    InfrastructureNetworkSnapPolicy,
    InfrastructureNetworkUnsnappedReason,
    validate_infrastructure_network_snap_batch,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _demand() -> BlockInfrastructureDemand:
    return BlockInfrastructureDemand(
        block_id="block-1",
        zone_id="zone-1",
        zone_class=ZoneClass.RESIDENTIAL,
        infrastructure_type_code="school.general",
        infrastructure_category=InfrastructureCategory.EDUCATION,
        demographic_signal=DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
        source_signal_value=20.0,
        demand_rate=0.5,
        gross_demand=10.0,
        served_demand=0.0,
        unmet_demand=10.0,
    )


def _facility() -> ExistingInfrastructureFacility:
    return ExistingInfrastructureFacility(
        facility_id="dataset:facilities:v1:school-1",
        source_ref="dataset:facilities:v1",
        source_feature_id="school-1",
        infrastructure_type_code="school.general",
        capacity=250.0,
        geometry=Point(10, 20),
        working_srid=WORKING_SRID,
    )


def _candidate() -> InfrastructureCandidateGeometry:
    site = box(0, 0, 10, 10)
    return InfrastructureCandidateGeometry(
        candidate_id="school.general:parcel:parcel-1",
        infrastructure_type_code="school.general",
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id="parcel-1",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(5, 5),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=site,
        site_area_m2=100.0,
        zone_id="zone-1",
        block_id="block-1",
    )


def test_stable_refs_are_derived_from_authoritative_s10_records() -> None:
    demand = InfrastructureDemandRef.from_demand(_demand())
    facility = ExistingInfrastructureFacilityRef.from_facility(_facility())
    candidate = InfrastructureCandidateRef.from_candidate(_candidate())

    assert demand.key == ("block-1", "school.general")
    assert facility.key == (
        "dataset:facilities:v1:school-1",
        "school.general",
    )
    assert candidate.key == (
        "school.general:parcel:parcel-1",
        "school.general",
    )


def test_subject_families_do_not_collapse_to_one_untyped_reference() -> None:
    demand = InfrastructureDemandRef(
        block_id="shared-id",
        infrastructure_type_code="school.general",
    )
    facility = ExistingInfrastructureFacilityRef(
        facility_id="shared-id",
        infrastructure_type_code="school.general",
    )
    candidate = InfrastructureCandidateRef(
        candidate_id="shared-id",
        infrastructure_type_code="school.general",
    )

    assert demand != facility
    assert facility != candidate
    assert demand != candidate


def test_snap_inputs_carry_metric_points_and_working_crs() -> None:
    point = NetworkPoint(x_m=10.0, y_m=20.0)

    demand = InfrastructureDemandSnapInput(
        ref=InfrastructureDemandRef.from_demand(_demand()),
        point=point,
        working_srid=WORKING_SRID,
    )
    facility = ExistingInfrastructureFacilitySnapInput(
        ref=ExistingInfrastructureFacilityRef.from_facility(_facility()),
        point=point,
        working_srid=WORKING_SRID,
    )
    candidate = InfrastructureCandidateSnapInput(
        ref=InfrastructureCandidateRef.from_candidate(_candidate()),
        point=point,
        working_srid=WORKING_SRID,
    )

    assert demand.point == point
    assert facility.point == point
    assert candidate.point == point
    assert demand.working_srid == WORKING_SRID
    assert facility.working_srid == WORKING_SRID
    assert candidate.working_srid == WORKING_SRID


def test_snap_input_rejects_non_metric_working_crs() -> None:
    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="projected",
    ):
        InfrastructureDemandSnapInput(
            ref=InfrastructureDemandRef.from_demand(_demand()),
            point=NetworkPoint(x_m=10.0, y_m=20.0),
            working_srid=4326,
        )


def test_successful_snap_records_preserve_typed_ref_node_and_distance() -> None:
    node = NetworkNodeRef(node_id="road-node-7")

    demand = InfrastructureDemandSnap(
        ref=InfrastructureDemandRef.from_demand(_demand()),
        node=node,
        distance_m=4.5,
    )
    facility = ExistingInfrastructureFacilitySnap(
        ref=ExistingInfrastructureFacilityRef.from_facility(_facility()),
        node=node,
        distance_m=0.0,
    )
    candidate = InfrastructureCandidateSnap(
        ref=InfrastructureCandidateRef.from_candidate(_candidate()),
        node=node,
        distance_m=12.0,
    )

    assert demand.node == node
    assert facility.node == node
    assert candidate.node == node
    assert demand.distance_m == pytest.approx(4.5)
    assert facility.distance_m == 0.0
    assert candidate.distance_m == pytest.approx(12.0)


@pytest.mark.parametrize(
    "distance_m",
    [-1.0, float("inf"), float("nan")],
)
def test_successful_snap_distance_must_be_non_negative_and_finite(
    distance_m: float,
) -> None:
    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="finite non-negative",
    ):
        InfrastructureDemandSnap(
            ref=InfrastructureDemandRef.from_demand(_demand()),
            node=NetworkNodeRef(node_id="road-node-7"),
            distance_m=distance_m,
        )


def test_ref_identifiers_reject_blank_and_line_break_values() -> None:
    with pytest.raises(InfrastructureNetworkSnapError):
        InfrastructureDemandRef(
            block_id="",
            infrastructure_type_code="school.general",
        )
    with pytest.raises(InfrastructureNetworkSnapError):
        InfrastructureCandidateRef(
            candidate_id="candidate\n1",
            infrastructure_type_code="school.general",
        )



def _snapshot(*, srid: int = WORKING_SRID, node_count: int = 2) -> NetworkGraphSnapshot:
    return NetworkGraphSnapshot(
        snapshot_id="roads:v1",
        working_crs=WorkingCRS(srid=srid),
        node_count=node_count,
        edge_count=max(node_count - 1, 0),
        directed=False,
    )


def _demand_snap_input(*, srid: int = WORKING_SRID) -> InfrastructureDemandSnapInput:
    return InfrastructureDemandSnapInput(
        ref=InfrastructureDemandRef.from_demand(_demand()),
        point=NetworkPoint(x_m=10.0, y_m=20.0),
        working_srid=srid,
    )


def test_snap_policy_has_explicit_metric_tolerance_and_hard_batch_bound() -> None:
    policy = InfrastructureNetworkSnapPolicy(max_snap_distance_m=25.0)

    assert policy.max_snap_distance_m == pytest.approx(25.0)
    assert policy.max_batch_size == MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE

    exact_only = InfrastructureNetworkSnapPolicy(
        max_snap_distance_m=0.0,
        max_batch_size=1,
    )
    assert exact_only.max_snap_distance_m == 0.0

    with pytest.raises(InfrastructureNetworkSnapError, match="finite non-negative"):
        InfrastructureNetworkSnapPolicy(max_snap_distance_m=-1.0)
    with pytest.raises(InfrastructureNetworkSnapError, match="hard limit"):
        InfrastructureNetworkSnapPolicy(
            max_snap_distance_m=10.0,
            max_batch_size=MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE + 1,
        )


def test_batch_contract_requires_exact_network_working_crs() -> None:
    validate_infrastructure_network_snap_batch(
        (_demand_snap_input(),),
        snapshot=_snapshot(),
        policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=25.0),
    )

    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="must match network snapshot",
    ):
        validate_infrastructure_network_snap_batch(
            (_demand_snap_input(srid=32637),),
            snapshot=_snapshot(),
            policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=25.0),
        )


def test_batch_contract_enforces_configured_batch_size() -> None:
    item = _demand_snap_input()
    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="batch size limit exceeded",
    ):
        validate_infrastructure_network_snap_batch(
            (item, item),
            snapshot=_snapshot(),
            policy=InfrastructureNetworkSnapPolicy(
                max_snap_distance_m=25.0,
                max_batch_size=1,
            ),
        )


def test_unsnapped_records_keep_subject_family_and_typed_reason() -> None:
    demand = InfrastructureDemandUnsnapped(
        ref=InfrastructureDemandRef.from_demand(_demand()),
        reason=InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE,
    )
    facility = ExistingInfrastructureFacilityUnsnapped(
        ref=ExistingInfrastructureFacilityRef.from_facility(_facility()),
        reason=InfrastructureNetworkUnsnappedReason.EMPTY_NETWORK,
    )
    candidate = InfrastructureCandidateUnsnapped(
        ref=InfrastructureCandidateRef.from_candidate(_candidate()),
        reason=InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE,
    )

    assert demand.reason is InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE
    assert facility.reason is InfrastructureNetworkUnsnappedReason.EMPTY_NETWORK
    assert candidate.reason is InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE


def test_snap_diagnostics_conserve_inputs_and_unsnapped_reasons() -> None:
    diagnostics = InfrastructureNetworkSnapDiagnostics(
        input_count=5,
        snapped_count=2,
        unsnapped_count=3,
        empty_network_count=0,
        no_node_within_max_distance_count=3,
    )

    assert diagnostics.input_count == 5

    with pytest.raises(InfrastructureNetworkSnapError, match="must equal input_count"):
        InfrastructureNetworkSnapDiagnostics(
            input_count=5,
            snapped_count=2,
            unsnapped_count=2,
            empty_network_count=0,
            no_node_within_max_distance_count=2,
        )

    with pytest.raises(InfrastructureNetworkSnapError, match="reason counts"):
        InfrastructureNetworkSnapDiagnostics(
            input_count=5,
            snapped_count=2,
            unsnapped_count=3,
            empty_network_count=1,
            no_node_within_max_distance_count=1,
        )
