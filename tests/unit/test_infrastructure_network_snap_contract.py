from __future__ import annotations

import networkx as nx
import pytest
from shapely.geometry import Point, box

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import (
    NetworkGraphSnapshot,
    NetworkNodeRef,
    NetworkPoint,
    NetworkSnapResult,
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
    snap_infrastructure_network_batch,
    validate_infrastructure_network_snap_batch,
)
from core.urban_generator.roads import NetworkXBackend
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



class _RecordingNetworkBackend:
    def __init__(
        self,
        *,
        snapshot: NetworkGraphSnapshot,
        responses: dict[NetworkPoint, NetworkSnapResult | None],
    ) -> None:
        self._snapshot = snapshot
        self.responses = responses
        self.calls: list[tuple[NetworkPoint, float]] = []

    @property
    def snapshot(self) -> NetworkGraphSnapshot:
        return self._snapshot

    def snap(
        self,
        point: NetworkPoint,
        *,
        max_distance_m: float,
    ) -> NetworkSnapResult | None:
        self.calls.append((point, max_distance_m))
        return self.responses.get(point)

    def shortest_path(self, *args, **kwargs):
        raise AssertionError("batch snapper must not call shortest_path")

    def multi_source_shortest_path(self, *args, **kwargs):
        raise AssertionError("batch snapper must not call multi_source_shortest_path")

    def multi_source_distances(self, *args, **kwargs):
        raise AssertionError("batch snapper must not call multi_source_distances")


def _candidate_snap_input() -> InfrastructureCandidateSnapInput:
    return InfrastructureCandidateSnapInput(
        ref=InfrastructureCandidateRef.from_candidate(_candidate()),
        point=NetworkPoint(x_m=5.0, y_m=5.0),
        working_srid=WORKING_SRID,
    )


def test_batch_snapper_uses_canonical_order_and_only_snap_port() -> None:
    demand = _demand_snap_input()
    candidate = _candidate_snap_input()
    backend = _RecordingNetworkBackend(
        snapshot=_snapshot(),
        responses={
            demand.point: NetworkSnapResult(
                node=NetworkNodeRef(node_id="node-demand"),
                distance_m=3.0,
            ),
            candidate.point: NetworkSnapResult(
                node=NetworkNodeRef(node_id="node-candidate"),
                distance_m=4.0,
            ),
        },
    )
    policy = InfrastructureNetworkSnapPolicy(max_snap_distance_m=25.0)

    result = snap_infrastructure_network_batch(
        backend,
        (candidate, demand),
        policy=policy,
    )

    assert backend.calls == [
        (demand.point, 25.0),
        (candidate.point, 25.0),
    ]
    assert [item.ref for item in result.snapped] == [
        demand.ref,
        candidate.ref,
    ]
    assert result.unsnapped == ()
    assert result.snapshot_id == "roads:v1"
    assert result.working_srid == WORKING_SRID
    assert result.diagnostics == InfrastructureNetworkSnapDiagnostics(
        input_count=2,
        snapped_count=2,
        unsnapped_count=0,
        empty_network_count=0,
        no_node_within_max_distance_count=0,
    )


def test_batch_snapper_rejects_duplicate_subject_identity_before_network_calls() -> None:
    demand = _demand_snap_input()
    backend = _RecordingNetworkBackend(
        snapshot=_snapshot(),
        responses={},
    )

    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="duplicate subject identities",
    ):
        snap_infrastructure_network_batch(
            backend,
            (demand, demand),
            policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=25.0),
        )

    assert backend.calls == []



def _real_backend(
    *,
    empty: bool = False,
) -> NetworkXBackend:
    graph = nx.Graph()
    if not empty:
        graph.add_node("a", x_m=0.0, y_m=0.0)
        graph.add_node("m", x_m=10.0, y_m=0.0)
        graph.add_node("z", x_m=20.0, y_m=0.0)
        graph.add_edge("a", "m", length_m=10.0)
        graph.add_edge("m", "z", length_m=10.0)
    return NetworkXBackend(
        graph,
        snapshot_id="roads:acceptance:v1",
        working_crs=WorkingCRS(srid=WORKING_SRID),
    )


def _typed_inputs_for_acceptance() -> tuple[
    InfrastructureDemandSnapInput,
    ExistingInfrastructureFacilitySnapInput,
    InfrastructureCandidateSnapInput,
]:
    return (
        InfrastructureDemandSnapInput(
            ref=InfrastructureDemandRef(
                block_id="block-exact",
                infrastructure_type_code="school.general",
            ),
            point=NetworkPoint(x_m=0.0, y_m=0.0),
            working_srid=WORKING_SRID,
        ),
        ExistingInfrastructureFacilitySnapInput(
            ref=ExistingInfrastructureFacilityRef(
                facility_id="facility-tie",
                infrastructure_type_code="school.general",
            ),
            point=NetworkPoint(x_m=15.0, y_m=0.0),
            working_srid=WORKING_SRID,
        ),
        InfrastructureCandidateSnapInput(
            ref=InfrastructureCandidateRef(
                candidate_id="candidate-outside",
                infrastructure_type_code="school.general",
            ),
            point=NetworkPoint(x_m=100.0, y_m=0.0),
            working_srid=WORKING_SRID,
        ),
    )


def test_acceptance_exact_hit_returns_zero_distance() -> None:
    demand = _typed_inputs_for_acceptance()[0]

    result = snap_infrastructure_network_batch(
        _real_backend(),
        (demand,),
        policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=10.0),
    )

    assert result.snapped == (
        InfrastructureDemandSnap(
            ref=demand.ref,
            node=NetworkNodeRef(node_id="a"),
            distance_m=0.0,
        ),
    )
    assert result.unsnapped == ()


def test_acceptance_equal_distance_tie_uses_stable_network_node_id() -> None:
    facility = _typed_inputs_for_acceptance()[1]

    result = snap_infrastructure_network_batch(
        _real_backend(),
        (facility,),
        policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=5.0),
    )

    assert result.snapped == (
        ExistingInfrastructureFacilitySnap(
            ref=facility.ref,
            node=NetworkNodeRef(node_id="m"),
            distance_m=5.0,
        ),
    )


def test_acceptance_outside_limit_is_typed_unsnapped_result() -> None:
    candidate = _typed_inputs_for_acceptance()[2]

    result = snap_infrastructure_network_batch(
        _real_backend(),
        (candidate,),
        policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=10.0),
    )

    assert result.snapped == ()
    assert result.unsnapped == (
        InfrastructureCandidateUnsnapped(
            ref=candidate.ref,
            reason=(
                InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE
            ),
        ),
    )
    assert result.diagnostics.no_node_within_max_distance_count == 1


def test_acceptance_empty_network_accounts_for_every_subject() -> None:
    inputs = _typed_inputs_for_acceptance()

    result = snap_infrastructure_network_batch(
        _real_backend(empty=True),
        inputs,
        policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=10.0),
    )

    assert result.snapped == ()
    assert len(result.unsnapped) == 3
    assert all(
        item.reason is InfrastructureNetworkUnsnappedReason.EMPTY_NETWORK
        for item in result.unsnapped
    )
    assert result.diagnostics == InfrastructureNetworkSnapDiagnostics(
        input_count=3,
        snapped_count=0,
        unsnapped_count=3,
        empty_network_count=3,
        no_node_within_max_distance_count=0,
    )


def test_acceptance_mixed_results_and_input_permutation_are_identical() -> None:
    demand, facility, candidate = _typed_inputs_for_acceptance()
    policy = InfrastructureNetworkSnapPolicy(max_snap_distance_m=5.0)
    backend = _real_backend()

    first = snap_infrastructure_network_batch(
        backend,
        (candidate, facility, demand),
        policy=policy,
    )
    second = snap_infrastructure_network_batch(
        _real_backend(),
        (demand, candidate, facility),
        policy=policy,
    )

    assert first == second
    assert [item.ref for item in first.snapped] == [
        demand.ref,
        facility.ref,
    ]
    assert first.unsnapped == (
        InfrastructureCandidateUnsnapped(
            ref=candidate.ref,
            reason=(
                InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE
            ),
        ),
    )
    assert first.diagnostics == InfrastructureNetworkSnapDiagnostics(
        input_count=3,
        snapped_count=2,
        unsnapped_count=1,
        empty_network_count=0,
        no_node_within_max_distance_count=1,
    )
