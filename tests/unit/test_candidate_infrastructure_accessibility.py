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
    InfrastructureAccessibilityError,
    InfrastructureCandidateAccessibilityPolicy,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureDemandSnap,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapDiagnostics,
    InfrastructureType,
    compute_candidate_site_accessibility,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857
TYPE_CODE = "school.general"


class _DistanceBackend:
    def __init__(
        self,
        *,
        distances: dict[tuple[str, str], float],
        snapshot_id: str = "roads:v1",
        srid: int = WORKING_SRID,
    ) -> None:
        self._snapshot = NetworkGraphSnapshot(
            snapshot_id=snapshot_id,
            working_crs=WorkingCRS(srid=srid),
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
        raise AssertionError("candidate accessibility must not call snap")

    def shortest_path(self, *args, **kwargs):
        raise AssertionError("candidate accessibility must not call shortest_path")

    def multi_source_shortest_path(self, *args, **kwargs):
        raise AssertionError(
            "candidate accessibility must not call multi_source_shortest_path"
        )

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        self.calls.append((sources, targets, max_distance_m))
        assert len(sources) == 1
        source = sources[0]
        results: list[NetworkDistanceResult] = []
        for target in targets:
            distance = self.distances.get((source.node_id, target.node_id))
            if distance is None:
                continue
            if max_distance_m is not None and distance > max_distance_m:
                continue
            results.append(
                NetworkDistanceResult(
                    source=source,
                    target=target,
                    distance_m=distance,
                )
            )
        return tuple(results)


def _type(
    *,
    code: str = TYPE_CODE,
    max_network_distance_m: float = 1_500.0,
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=code,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=0.8,
        ),
        capacity=600.0,
        max_network_distance_m=max_network_distance_m,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=6_000.0,
        target_site_area_m2=12_000.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _demand(block_id: str, node_id: str, *, type_code: str = TYPE_CODE):
    return InfrastructureDemandSnap(
        ref=InfrastructureDemandRef(
            block_id=block_id,
            infrastructure_type_code=type_code,
        ),
        node=NetworkNodeRef(node_id=node_id),
        distance_m=1.0,
    )


def _candidate(candidate_id: str, node_id: str, *, type_code: str = TYPE_CODE):
    return InfrastructureCandidateSnap(
        ref=InfrastructureCandidateRef(
            candidate_id=candidate_id,
            infrastructure_type_code=type_code,
        ),
        node=NetworkNodeRef(node_id=node_id),
        distance_m=1.0,
    )


def _batch(
    *snapped: InfrastructureDemandSnap | InfrastructureCandidateSnap,
    snapshot_id: str = "roads:v1",
    srid: int = WORKING_SRID,
) -> InfrastructureNetworkSnapBatchResult:
    def sort_key(
        item: InfrastructureDemandSnap | InfrastructureCandidateSnap,
    ) -> tuple[int, tuple[str, str]]:
        rank = 0 if isinstance(item, InfrastructureDemandSnap) else 2
        return rank, item.ref.key

    ordered = tuple(sorted(snapped, key=sort_key))
    return InfrastructureNetworkSnapBatchResult(
        snapshot_id=snapshot_id,
        working_srid=srid,
        snapped=ordered,
        unsnapped=(),
        diagnostics=InfrastructureNetworkSnapDiagnostics(
            input_count=len(ordered),
            snapped_count=len(ordered),
            unsnapped_count=0,
            empty_network_count=0,
            no_node_within_max_distance_count=0,
        ),
    )


def _policy(
    *,
    demand_batch_size: int = 2,
    max_routing_calls: int = 10,
    max_results: int = 100,
) -> InfrastructureCandidateAccessibilityPolicy:
    return InfrastructureCandidateAccessibilityPolicy(
        max_candidates=10,
        max_demands=10,
        demand_batch_size=demand_batch_size,
        max_routing_calls=max_routing_calls,
        max_results=max_results,
    )


def test_candidate_accessibility_batches_demand_targets_per_candidate_node() -> None:
    backend = _DistanceBackend(
        distances={
            ("candidate-1-node", "demand-a-node"): 100.0,
            ("candidate-1-node", "demand-b-node"): 200.0,
            ("candidate-1-node", "demand-c-node"): 300.0,
            ("candidate-2-node", "demand-a-node"): 400.0,
            ("candidate-2-node", "demand-b-node"): 500.0,
            ("candidate-2-node", "demand-c-node"): 1_600.0,
        }
    )
    snap_batch = _batch(
        _demand("block-c", "demand-c-node"),
        _candidate("candidate-2", "candidate-2-node"),
        _demand("block-a", "demand-a-node"),
        _candidate("candidate-1", "candidate-1-node"),
        _demand("block-b", "demand-b-node"),
    )

    result = compute_candidate_site_accessibility(
        backend,
        snap_batch,
        infrastructure_type=_type(),
        policy=_policy(demand_batch_size=2),
    )

    assert backend.calls == [
        (
            (NetworkNodeRef(node_id="candidate-1-node"),),
            (
                NetworkNodeRef(node_id="demand-a-node"),
                NetworkNodeRef(node_id="demand-b-node"),
            ),
            1_500.0,
        ),
        (
            (NetworkNodeRef(node_id="candidate-1-node"),),
            (NetworkNodeRef(node_id="demand-c-node"),),
            1_500.0,
        ),
        (
            (NetworkNodeRef(node_id="candidate-2-node"),),
            (
                NetworkNodeRef(node_id="demand-a-node"),
                NetworkNodeRef(node_id="demand-b-node"),
            ),
            1_500.0,
        ),
        (
            (NetworkNodeRef(node_id="candidate-2-node"),),
            (NetworkNodeRef(node_id="demand-c-node"),),
            1_500.0,
        ),
    ]
    assert [item.key for item in result] == [
        (TYPE_CODE, "block-a", "candidate_site", "candidate-1"),
        (TYPE_CODE, "block-a", "candidate_site", "candidate-2"),
        (TYPE_CODE, "block-b", "candidate_site", "candidate-1"),
        (TYPE_CODE, "block-b", "candidate_site", "candidate-2"),
        (TYPE_CODE, "block-c", "candidate_site", "candidate-1"),
    ]


def test_candidate_accessibility_deduplicates_shared_nodes_and_fans_out_refs() -> None:
    backend = _DistanceBackend(
        distances={
            ("candidate-node", "demand-node"): 125.0,
        }
    )
    snap_batch = _batch(
        _candidate("candidate-b", "candidate-node"),
        _demand("block-b", "demand-node"),
        _candidate("candidate-a", "candidate-node"),
        _demand("block-a", "demand-node"),
    )

    result = compute_candidate_site_accessibility(
        backend,
        snap_batch,
        infrastructure_type=_type(),
        policy=_policy(),
    )

    assert len(backend.calls) == 1
    assert [item.key for item in result] == [
        (TYPE_CODE, "block-a", "candidate_site", "candidate-a"),
        (TYPE_CODE, "block-a", "candidate_site", "candidate-b"),
        (TYPE_CODE, "block-b", "candidate_site", "candidate-a"),
        (TYPE_CODE, "block-b", "candidate_site", "candidate-b"),
    ]
    assert {item.distance_m for item in result} == {125.0}


def test_candidate_accessibility_rejects_pair_budget_before_routing() -> None:
    backend = _DistanceBackend(distances={})
    snap_batch = _batch(
        _candidate("candidate-a", "candidate-a-node"),
        _candidate("candidate-b", "candidate-b-node"),
        _demand("block-a", "demand-a-node"),
        _demand("block-b", "demand-b-node"),
    )

    with pytest.raises(
        InfrastructureAccessibilityError,
        match="result budget exceeded before routing",
    ):
        compute_candidate_site_accessibility(
            backend,
            snap_batch,
            infrastructure_type=_type(),
            policy=_policy(max_results=3),
        )

    assert backend.calls == []


def test_candidate_accessibility_rejects_routing_call_budget_before_routing() -> None:
    backend = _DistanceBackend(distances={})
    snap_batch = _batch(
        _candidate("candidate-a", "candidate-a-node"),
        _candidate("candidate-b", "candidate-b-node"),
        _demand("block-a", "demand-a-node"),
        _demand("block-b", "demand-b-node"),
        _demand("block-c", "demand-c-node"),
    )

    with pytest.raises(
        InfrastructureAccessibilityError,
        match="routing-call budget exceeded before routing",
    ):
        compute_candidate_site_accessibility(
            backend,
            snap_batch,
            infrastructure_type=_type(),
            policy=_policy(
                demand_batch_size=1,
                max_routing_calls=5,
            ),
        )

    assert backend.calls == []


def test_candidate_accessibility_filters_to_requested_type() -> None:
    backend = _DistanceBackend(
        distances={
            ("school-candidate-node", "school-demand-node"): 300.0,
        }
    )
    snap_batch = _batch(
        _candidate("school-candidate", "school-candidate-node"),
        _candidate(
            "clinic-candidate",
            "clinic-candidate-node",
            type_code="clinic.primary",
        ),
        _demand("school-block", "school-demand-node"),
        _demand("clinic-block", "clinic-demand-node", type_code="clinic.primary"),
    )

    result = compute_candidate_site_accessibility(
        backend,
        snap_batch,
        infrastructure_type=_type(),
        policy=_policy(),
    )

    assert len(result) == 1
    assert result[0].demand_ref.block_id == "school-block"
    assert result[0].facility_site_ref == InfrastructureCandidateRef(
        candidate_id="school-candidate",
        infrastructure_type_code=TYPE_CODE,
    )


def test_candidate_accessibility_rejects_snapshot_mismatch_before_routing() -> None:
    backend = _DistanceBackend(distances={}, snapshot_id="roads:v2")

    with pytest.raises(
        InfrastructureAccessibilityError,
        match="snapshot_id must match",
    ):
        compute_candidate_site_accessibility(
            backend,
            _batch(
                _candidate("candidate-a", "candidate-node"),
                _demand("block-a", "demand-node"),
            ),
            infrastructure_type=_type(),
            policy=_policy(),
        )

    assert backend.calls == []
