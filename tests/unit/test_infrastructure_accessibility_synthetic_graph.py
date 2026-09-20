from __future__ import annotations

import networkx as nx
import pytest

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import NetworkDistanceResult, NetworkNodeRef, WorkingCRS
from core.urban_generator.infrastructure import (
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnap,
    InfrastructureAccessibilityUnavailableReason,
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
    compute_candidate_site_accessibility_batch,
    compute_existing_facility_accessibility_batch,
)
from core.urban_generator.roads import NetworkXBackend, NetworkXBackendError
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857
SNAPSHOT_ID = "roads:accessibility-acceptance"
TYPE_CODE = "school.general"


class _CountingNetworkXBackend(NetworkXBackend):
    def __init__(
        self,
        graph: nx.Graph,
        *,
        max_routing_visited_nodes: int,
    ) -> None:
        self.distance_calls: list[
            tuple[
                tuple[NetworkNodeRef, ...],
                tuple[NetworkNodeRef, ...],
                float | None,
            ]
        ] = []
        super().__init__(
            graph,
            snapshot_id=SNAPSHOT_ID,
            working_crs=WorkingCRS(srid=WORKING_SRID),
            max_routing_visited_nodes=max_routing_visited_nodes,
        )

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        self.distance_calls.append((sources, targets, max_distance_m))
        return super().multi_source_distances(
            sources,
            targets,
            max_distance_m=max_distance_m,
        )


def _type(*, max_network_distance_m: float) -> InfrastructureType:
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
        max_network_distance_m=max_network_distance_m,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=6_000.0,
        target_site_area_m2=12_000.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _policy(
    *,
    demand_batch_size: int,
    max_routing_calls: int,
    max_results: int,
) -> InfrastructureCandidateAccessibilityPolicy:
    return InfrastructureCandidateAccessibilityPolicy(
        max_candidates=10,
        max_demands=10,
        demand_batch_size=demand_batch_size,
        max_routing_calls=max_routing_calls,
        max_results=max_results,
    )


def _demand(block_id: str, node_id: str) -> InfrastructureDemandSnap:
    return InfrastructureDemandSnap(
        ref=InfrastructureDemandRef(
            block_id=block_id,
            infrastructure_type_code=TYPE_CODE,
        ),
        node=NetworkNodeRef(node_id=node_id),
        distance_m=0.0,
    )


def _facility(facility_id: str, node_id: str) -> ExistingInfrastructureFacilitySnap:
    return ExistingInfrastructureFacilitySnap(
        ref=ExistingInfrastructureFacilityRef(
            facility_id=facility_id,
            infrastructure_type_code=TYPE_CODE,
        ),
        node=NetworkNodeRef(node_id=node_id),
        distance_m=0.0,
    )


def _candidate(candidate_id: str, node_id: str) -> InfrastructureCandidateSnap:
    return InfrastructureCandidateSnap(
        ref=InfrastructureCandidateRef(
            candidate_id=candidate_id,
            infrastructure_type_code=TYPE_CODE,
        ),
        node=NetworkNodeRef(node_id=node_id),
        distance_m=0.0,
    )


def _batch(
    *items: (
        InfrastructureDemandSnap
        | ExistingInfrastructureFacilitySnap
        | InfrastructureCandidateSnap
    ),
) -> InfrastructureNetworkSnapBatchResult:
    def sort_key(
        item: (
            InfrastructureDemandSnap
            | ExistingInfrastructureFacilitySnap
            | InfrastructureCandidateSnap
        ),
    ) -> tuple[int, tuple[str, str]]:
        if isinstance(item, InfrastructureDemandSnap):
            return 0, item.ref.key
        if isinstance(item, ExistingInfrastructureFacilitySnap):
            return 1, item.ref.key
        return 2, item.ref.key

    snapped = tuple(sorted(items, key=sort_key))
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


def _diamond_graph(*, reverse_insertion: bool) -> nx.Graph:
    graph = nx.Graph()
    nodes = [
        ("site-a", -100.0, 0.0),
        ("site-z", 100.0, 0.0),
        ("junction", 0.0, 0.0),
        ("demand-main", 0.0, 100.0),
        ("demand-disconnected", 0.0, 1_000.0),
    ]
    edges = [
        ("site-a", "junction", 100.0),
        ("site-z", "junction", 100.0),
        ("junction", "demand-main", 100.0),
    ]
    if reverse_insertion:
        nodes.reverse()
        edges.reverse()

    for node_id, x_m, y_m in nodes:
        graph.add_node(node_id, x_m=x_m, y_m=y_m)
    for source_id, target_id, length_m in edges:
        graph.add_edge(source_id, target_id, length_m=length_m)
    return graph


def _line_graph() -> nx.Graph:
    graph = nx.Graph()
    for index in range(11):
        node_id = f"n{index:02d}"
        graph.add_node(node_id, x_m=float(index * 100), y_m=0.0)
    for index in range(10):
        graph.add_edge(
            f"n{index:02d}",
            f"n{index + 1:02d}",
            length_m=100.0,
        )
    return graph


def _diamond_batch() -> InfrastructureNetworkSnapBatchResult:
    return _batch(
        _demand("block-disconnected", "demand-disconnected"),
        _facility("facility-z", "site-z"),
        _candidate("candidate-z", "site-z"),
        _demand("block-main", "demand-main"),
        _candidate("candidate-a", "site-a"),
        _facility("facility-a", "site-a"),
    )


def _line_batch() -> InfrastructureNetworkSnapBatchResult:
    return _batch(
        _candidate("candidate-z", "n10"),
        _demand("block-08", "n08"),
        _demand("block-02", "n02"),
        _candidate("candidate-a", "n00"),
        _demand("block-06", "n06"),
        _demand("block-04", "n04"),
    )


def test_synthetic_graph_accessibility_is_deterministic_across_graph_insertion_order() -> None:
    infrastructure_type = _type(max_network_distance_m=250.0)
    policy = _policy(
        demand_batch_size=2,
        max_routing_calls=2,
        max_results=4,
    )
    snap_batch = _diamond_batch()

    first = _CountingNetworkXBackend(
        _diamond_graph(reverse_insertion=False),
        max_routing_visited_nodes=4,
    )
    second = _CountingNetworkXBackend(
        _diamond_graph(reverse_insertion=True),
        max_routing_visited_nodes=4,
    )

    first_existing = compute_existing_facility_accessibility_batch(
        first,
        snap_batch,
        infrastructure_type=infrastructure_type,
    )
    second_existing = compute_existing_facility_accessibility_batch(
        second,
        snap_batch,
        infrastructure_type=infrastructure_type,
    )
    first_candidates = compute_candidate_site_accessibility_batch(
        first,
        snap_batch,
        infrastructure_type=infrastructure_type,
        policy=policy,
    )
    second_candidates = compute_candidate_site_accessibility_batch(
        second,
        snap_batch,
        infrastructure_type=infrastructure_type,
        policy=policy,
    )

    assert first_existing == second_existing
    assert first_candidates == second_candidates
    assert [item.key for item in first_existing.reachable] == [
        (TYPE_CODE, "block-main", "existing_facility", "facility-a")
    ]
    assert first_existing.unavailable[0].reason is (
        InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE
    )
    assert [item.key for item in first_candidates.reachable] == [
        (TYPE_CODE, "block-main", "candidate_site", "candidate-a"),
        (TYPE_CODE, "block-main", "candidate_site", "candidate-z"),
    ]
    assert {
        item.reason for item in first_candidates.unavailable
    } == {
        InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE
    }
    assert len(first.distance_calls) == 3
    assert len(second.distance_calls) == 3


def test_candidate_accessibility_real_graph_stays_within_call_and_search_budgets() -> None:
    backend = _CountingNetworkXBackend(
        _line_graph(),
        max_routing_visited_nodes=5,
    )
    policy = _policy(
        demand_batch_size=2,
        max_routing_calls=4,
        max_results=8,
    )

    result = compute_candidate_site_accessibility_batch(
        backend,
        _line_batch(),
        infrastructure_type=_type(max_network_distance_m=400.0),
        policy=policy,
    )

    assert len(backend.distance_calls) == 4
    assert all(len(sources) == 1 for sources, _, _ in backend.distance_calls)
    assert all(len(targets) == 2 for _, targets, _ in backend.distance_calls)
    assert all(
        max_distance_m == 400.0
        for _, _, max_distance_m in backend.distance_calls
    )
    assert result.diagnostics.subject_count == 8
    assert result.diagnostics.reachable_count == 4
    assert result.diagnostics.unavailable_count == 4
    assert [item.key for item in result.reachable] == [
        (TYPE_CODE, "block-02", "candidate_site", "candidate-a"),
        (TYPE_CODE, "block-04", "candidate_site", "candidate-a"),
        (TYPE_CODE, "block-06", "candidate_site", "candidate-z"),
        (TYPE_CODE, "block-08", "candidate_site", "candidate-z"),
    ]


def test_candidate_accessibility_real_graph_fails_fast_when_search_budget_is_too_small() -> None:
    backend = _CountingNetworkXBackend(
        _line_graph(),
        max_routing_visited_nodes=4,
    )

    with pytest.raises(
        NetworkXBackendError,
        match="routing visit limit exceeded: 5 > 4",
    ):
        compute_candidate_site_accessibility_batch(
            backend,
            _line_batch(),
            infrastructure_type=_type(max_network_distance_m=400.0),
            policy=_policy(
                demand_batch_size=2,
                max_routing_calls=4,
                max_results=8,
            ),
        )

    assert len(backend.distance_calls) == 1
