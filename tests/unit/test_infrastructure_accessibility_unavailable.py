from __future__ import annotations

import pytest

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import (
    NetworkDistanceResult,
    NetworkGraphSnapshot,
    NetworkNodeRef,
    WorkingCRS,
)
from core.urban_generator.infrastructure import (
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnap,
    ExistingInfrastructureFacilityUnsnapped,
    InfrastructureAccessibilityMode,
    InfrastructureAccessibilityUnavailableReason,
    InfrastructureCandidateAccessibilityPolicy,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateSource,
    InfrastructureCandidateUnsnapped,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureDemandSnap,
    InfrastructureDemandUnsnapped,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapDiagnostics,
    InfrastructureNetworkUnsnappedReason,
    InfrastructureType,
    compute_candidate_site_accessibility_batch,
    compute_existing_facility_accessibility_batch,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857
TYPE_CODE = "school.general"


class _DistanceBackend:
    def __init__(self, distances: dict[tuple[str, str], float]) -> None:
        self._snapshot = NetworkGraphSnapshot(
            snapshot_id="roads:v1",
            working_crs=WorkingCRS(srid=WORKING_SRID),
            node_count=50,
            edge_count=49,
            directed=False,
        )
        self.distances = distances
        self.calls: list[
            tuple[
                tuple[NetworkNodeRef, ...],
                tuple[NetworkNodeRef, ...],
                float | None,
            ]
        ] = []

    @property
    def snapshot(self) -> NetworkGraphSnapshot:
        return self._snapshot

    def snap(self, *args, **kwargs):
        raise AssertionError

    def shortest_path(self, *args, **kwargs):
        raise AssertionError

    def multi_source_shortest_path(self, *args, **kwargs):
        raise AssertionError

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        self.calls.append((sources, targets, max_distance_m))
        results: list[NetworkDistanceResult] = []
        for target in targets:
            best: tuple[float, NetworkNodeRef] | None = None
            for source in sources:
                distance = self.distances.get((source.node_id, target.node_id))
                if distance is None:
                    continue
                if max_distance_m is not None and distance > max_distance_m:
                    continue
                candidate = (distance, source)
                if best is None or (candidate[0], candidate[1].node_id) < (
                    best[0],
                    best[1].node_id,
                ):
                    best = candidate
            if best is not None:
                results.append(
                    NetworkDistanceResult(
                        source=best[1],
                        target=target,
                        distance_m=best[0],
                    )
                )
        return tuple(results)


def _type() -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=0.8,
        ),
        capacity=600.0,
        max_network_distance_m=1_500.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=6_000.0,
        target_site_area_m2=12_000.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _demand_ref(block_id: str) -> InfrastructureDemandRef:
    return InfrastructureDemandRef(
        block_id=block_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _candidate_ref(candidate_id: str) -> InfrastructureCandidateRef:
    return InfrastructureCandidateRef(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _facility_ref(facility_id: str) -> ExistingInfrastructureFacilityRef:
    return ExistingInfrastructureFacilityRef(
        facility_id=facility_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _batch(
    *,
    snapped: tuple[
        InfrastructureDemandSnap
        | InfrastructureCandidateSnap
        | ExistingInfrastructureFacilitySnap,
        ...,
    ],
    unsnapped: tuple[
        InfrastructureDemandUnsnapped
        | InfrastructureCandidateUnsnapped
        | ExistingInfrastructureFacilityUnsnapped,
        ...,
    ],
) -> InfrastructureNetworkSnapBatchResult:
    def rank(
        item: (
            InfrastructureDemandSnap
            | InfrastructureCandidateSnap
            | ExistingInfrastructureFacilitySnap
            | InfrastructureDemandUnsnapped
            | InfrastructureCandidateUnsnapped
            | ExistingInfrastructureFacilityUnsnapped
        ),
    ) -> tuple[int, tuple[str, str]]:
        if isinstance(item.ref, InfrastructureDemandRef):
            return 0, item.ref.key
        if isinstance(item.ref, ExistingInfrastructureFacilityRef):
            return 1, item.ref.key
        return 2, item.ref.key

    snapped_ordered = tuple(sorted(snapped, key=rank))
    unsnapped_ordered = tuple(sorted(unsnapped, key=rank))
    empty_count = sum(
        item.reason is InfrastructureNetworkUnsnappedReason.EMPTY_NETWORK
        for item in unsnapped_ordered
    )
    no_node_count = len(unsnapped_ordered) - empty_count
    return InfrastructureNetworkSnapBatchResult(
        snapshot_id="roads:v1",
        working_srid=WORKING_SRID,
        snapped=snapped_ordered,
        unsnapped=unsnapped_ordered,
        diagnostics=InfrastructureNetworkSnapDiagnostics(
            input_count=len(snapped_ordered) + len(unsnapped_ordered),
            snapped_count=len(snapped_ordered),
            unsnapped_count=len(unsnapped_ordered),
            empty_network_count=empty_count,
            no_node_within_max_distance_count=no_node_count,
        ),
    )


def _candidate_policy(*, max_results: int = 100):
    return InfrastructureCandidateAccessibilityPolicy(
        max_candidates=10,
        max_demands=10,
        demand_batch_size=10,
        max_routing_calls=10,
        max_results=max_results,
    )


def test_candidate_batch_materializes_typed_unavailable_reasons_without_distance() -> None:
    backend = _DistanceBackend(
        {
            ("candidate-a-node", "demand-a-node"): 100.0,
        }
    )
    snap_batch = _batch(
        snapped=(
            InfrastructureDemandSnap(
                ref=_demand_ref("block-a"),
                node=NetworkNodeRef("demand-a-node"),
                distance_m=1.0,
            ),
            InfrastructureDemandSnap(
                ref=_demand_ref("block-c"),
                node=NetworkNodeRef("demand-c-node"),
                distance_m=1.0,
            ),
            InfrastructureCandidateSnap(
                ref=_candidate_ref("candidate-a"),
                node=NetworkNodeRef("candidate-a-node"),
                distance_m=1.0,
            ),
        ),
        unsnapped=(
            InfrastructureDemandUnsnapped(
                ref=_demand_ref("block-b"),
                reason=InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE,
            ),
            InfrastructureCandidateUnsnapped(
                ref=_candidate_ref("candidate-b"),
                reason=InfrastructureNetworkUnsnappedReason.EMPTY_NETWORK,
            ),
        ),
    )

    batch = compute_candidate_site_accessibility_batch(
        backend,
        snap_batch,
        infrastructure_type=_type(),
        policy=_candidate_policy(),
    )

    assert batch.mode is InfrastructureAccessibilityMode.CANDIDATE_SITE
    assert len(batch.reachable) == 1
    assert batch.reachable[0].distance_m == pytest.approx(100.0)
    reasons = {item.key: item.reason for item in batch.unavailable}
    assert reasons[(TYPE_CODE, "block-a", "candidate_site", "candidate-b")] is (
        InfrastructureAccessibilityUnavailableReason.FACILITY_SITE_UNSNAPPED
    )
    assert reasons[(TYPE_CODE, "block-b", "candidate_site", "candidate-a")] is (
        InfrastructureAccessibilityUnavailableReason.DEMAND_UNSNAPPED
    )
    assert reasons[(TYPE_CODE, "block-b", "candidate_site", "candidate-b")] is (
        InfrastructureAccessibilityUnavailableReason.BOTH_UNSNAPPED
    )
    assert reasons[(TYPE_CODE, "block-c", "candidate_site", "candidate-a")] is (
        InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE
    )
    assert all(not hasattr(item, "distance_m") for item in batch.unavailable)
    assert batch.diagnostics.subject_count == 6
    assert batch.diagnostics.reachable_count == 1
    assert batch.diagnostics.unavailable_count == 5


def test_existing_batch_uses_demand_level_unavailable_outcomes() -> None:
    backend = _DistanceBackend(
        {
            ("facility-node", "demand-a-node"): 250.0,
        }
    )
    snap_batch = _batch(
        snapped=(
            InfrastructureDemandSnap(
                ref=_demand_ref("block-a"),
                node=NetworkNodeRef("demand-a-node"),
                distance_m=1.0,
            ),
            InfrastructureDemandSnap(
                ref=_demand_ref("block-b"),
                node=NetworkNodeRef("demand-b-node"),
                distance_m=1.0,
            ),
            ExistingInfrastructureFacilitySnap(
                ref=_facility_ref("facility-a"),
                node=NetworkNodeRef("facility-node"),
                distance_m=1.0,
            ),
        ),
        unsnapped=(
            InfrastructureDemandUnsnapped(
                ref=_demand_ref("block-c"),
                reason=InfrastructureNetworkUnsnappedReason.EMPTY_NETWORK,
            ),
        ),
    )

    batch = compute_existing_facility_accessibility_batch(
        backend,
        snap_batch,
        infrastructure_type=_type(),
    )

    assert batch.mode is InfrastructureAccessibilityMode.EXISTING_FACILITY
    assert [item.demand_ref.block_id for item in batch.reachable] == ["block-a"]
    unavailable = {item.demand_ref.block_id: item for item in batch.unavailable}
    assert unavailable["block-b"].reason is (
        InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE
    )
    assert unavailable["block-c"].reason is (
        InfrastructureAccessibilityUnavailableReason.DEMAND_UNSNAPPED
    )
    assert all(item.facility_site_ref is None for item in batch.unavailable)
    assert all(not hasattr(item, "distance_m") for item in batch.unavailable)


def test_existing_batch_reports_no_snapped_facility_without_numeric_sentinel() -> None:
    backend = _DistanceBackend({})
    snap_batch = _batch(
        snapped=(
            InfrastructureDemandSnap(
                ref=_demand_ref("block-a"),
                node=NetworkNodeRef("demand-node"),
                distance_m=1.0,
            ),
        ),
        unsnapped=(
            ExistingInfrastructureFacilityUnsnapped(
                ref=_facility_ref("facility-a"),
                reason=InfrastructureNetworkUnsnappedReason.NO_NODE_WITHIN_MAX_DISTANCE,
            ),
        ),
    )

    batch = compute_existing_facility_accessibility_batch(
        backend,
        snap_batch,
        infrastructure_type=_type(),
    )

    assert batch.reachable == ()
    assert len(batch.unavailable) == 1
    assert batch.unavailable[0].reason is (
        InfrastructureAccessibilityUnavailableReason.NO_SNAPPED_FACILITY_SITE
    )
    assert not hasattr(batch.unavailable[0], "distance_m")
    assert backend.calls == []


def test_candidate_batch_pair_budget_counts_unsnapped_subjects_before_routing() -> None:
    backend = _DistanceBackend({})
    snap_batch = _batch(
        snapped=(
            InfrastructureDemandSnap(
                ref=_demand_ref("block-a"),
                node=NetworkNodeRef("demand-node"),
                distance_m=1.0,
            ),
            InfrastructureCandidateSnap(
                ref=_candidate_ref("candidate-a"),
                node=NetworkNodeRef("candidate-node"),
                distance_m=1.0,
            ),
        ),
        unsnapped=(
            InfrastructureDemandUnsnapped(
                ref=_demand_ref("block-b"),
                reason=InfrastructureNetworkUnsnappedReason.EMPTY_NETWORK,
            ),
            InfrastructureCandidateUnsnapped(
                ref=_candidate_ref("candidate-b"),
                reason=InfrastructureNetworkUnsnappedReason.EMPTY_NETWORK,
            ),
        ),
    )

    with pytest.raises(Exception, match="result budget exceeded before routing"):
        compute_candidate_site_accessibility_batch(
            backend,
            snap_batch,
            infrastructure_type=_type(),
            policy=_candidate_policy(max_results=3),
        )

    assert backend.calls == []
