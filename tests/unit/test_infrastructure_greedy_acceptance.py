from __future__ import annotations

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import NetworkNodeRef
from core.urban_generator.infrastructure import (
    BlockInfrastructureDemand,
    InfrastructureAccessibilityBatchResult,
    InfrastructureAccessibilityDiagnostics,
    InfrastructureAccessibilityMode,
    InfrastructureAccessibilityResult,
    InfrastructureAccessibilityUnavailable,
    InfrastructureAccessibilityUnavailableReason,
    InfrastructureCandidateBenefit,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureFeasibilityResult,
    InfrastructureGreedyPlacementPolicy,
    InfrastructureGreedyPlacementState,
    InfrastructurePlacementSelection,
    InfrastructurePlacementSelectionStatus,
    InfrastructureType,
    apply_infrastructure_greedy_selection,
    calculate_infrastructure_candidate_benefits,
    initialize_infrastructure_greedy_placement_state,
    select_infrastructure_greedy_candidate,
)
from core.urban_generator.zoning import ZoneClass

SNAPSHOT_ID = "roads:greedy-acceptance-v1"
TYPE_CODE = "school.general"


def _type(*, capacity: float) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=1.0,
        ),
        capacity=capacity,
        max_network_distance_m=1_500.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=6_000.0,
        target_site_area_m2=12_000.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _demand(block_id: str, amount: float) -> BlockInfrastructureDemand:
    return BlockInfrastructureDemand(
        block_id=block_id,
        zone_id=f"zone-{block_id}",
        zone_class=ZoneClass.PUBLIC,
        infrastructure_type_code=TYPE_CODE,
        infrastructure_category=InfrastructureCategory.EDUCATION,
        demographic_signal=DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
        source_signal_value=amount,
        demand_rate=1.0,
        gross_demand=amount,
        served_demand=0.0,
        unmet_demand=amount,
    )


def _candidate(candidate_id: str) -> InfrastructureCandidateRef:
    return InfrastructureCandidateRef(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _demand_ref(block_id: str) -> InfrastructureDemandRef:
    return InfrastructureDemandRef(
        block_id=block_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _accessibility(
    demands: tuple[BlockInfrastructureDemand, ...],
    candidates: tuple[InfrastructureCandidateRef, ...],
    reachable_distances: dict[tuple[str, str], float],
) -> InfrastructureAccessibilityBatchResult:
    reachable: list[InfrastructureAccessibilityResult] = []
    unavailable: list[InfrastructureAccessibilityUnavailable] = []

    for demand in demands:
        demand_ref = _demand_ref(demand.block_id)
        for candidate in candidates:
            key = (demand.block_id, candidate.candidate_id)
            distance = reachable_distances.get(key)
            if distance is None:
                unavailable.append(
                    InfrastructureAccessibilityUnavailable(
                        snapshot_id=SNAPSHOT_ID,
                        infrastructure_type_code=TYPE_CODE,
                        demand_ref=demand_ref,
                        facility_site_ref=candidate,
                        max_network_distance_m=1_500.0,
                        reason=(
                            InfrastructureAccessibilityUnavailableReason.
                            NO_PATH_WITHIN_MAX_DISTANCE
                        ),
                    )
                )
            else:
                reachable.append(
                    InfrastructureAccessibilityResult(
                        snapshot_id=SNAPSHOT_ID,
                        infrastructure_type_code=TYPE_CODE,
                        demand_ref=demand_ref,
                        facility_site_ref=candidate,
                        demand_node=NetworkNodeRef(
                            node_id=f"demand-{demand.block_id}"
                        ),
                        facility_site_node=NetworkNodeRef(
                            node_id=f"candidate-{candidate.candidate_id}"
                        ),
                        max_network_distance_m=1_500.0,
                        distance_m=distance,
                    )
                )

    reachable_result = tuple(sorted(reachable, key=lambda item: item.key))
    unavailable_result = tuple(sorted(unavailable, key=lambda item: item.key))
    return InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.CANDIDATE_SITE,
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        reachable=reachable_result,
        unavailable=unavailable_result,
        diagnostics=InfrastructureAccessibilityDiagnostics(
            subject_count=len(demands) * len(candidates),
            reachable_count=len(reachable_result),
            unavailable_count=len(unavailable_result),
            demand_unsnapped_count=0,
            facility_site_unsnapped_count=0,
            both_unsnapped_count=0,
            no_path_within_max_distance_count=len(unavailable_result),
            no_snapped_facility_site_count=0,
        ),
    )


def _feasibility_for_benefits(
    benefits: tuple[InfrastructureCandidateBenefit, ...],
) -> tuple[InfrastructureFeasibilityResult, ...]:
    return tuple(
        InfrastructureFeasibilityResult(
            candidate_id=item.candidate_ref.candidate_id,
            infrastructure_type_code=TYPE_CODE,
            working_srid=3857,
            geometry_kind=InfrastructureCandidateGeometryKind.SITE,
            proposed_capacity=item.capacity,
            is_feasible=True,
        )
        for item in benefits
    )


def _run_greedy(
    demands: tuple[BlockInfrastructureDemand, ...],
    candidates: tuple[InfrastructureCandidateRef, ...],
    *,
    reachable_distances: dict[tuple[str, str], float],
    infrastructure_type: InfrastructureType,
    policy: InfrastructureGreedyPlacementPolicy,
) -> tuple[InfrastructureGreedyPlacementState, InfrastructurePlacementSelection]:
    state = initialize_infrastructure_greedy_placement_state(
        demands,
        candidates,
        candidate_accessibility=_accessibility(
            demands,
            candidates,
            reachable_distances,
        ),
    )

    for iteration_index in range(policy.max_iterations):
        benefits = calculate_infrastructure_candidate_benefits(
            state,
            infrastructure_type=infrastructure_type,
        )
        selection = select_infrastructure_greedy_candidate(
            state,
            benefits,
            feasibility=_feasibility_for_benefits(benefits),
            iteration_index=iteration_index,
            policy=policy,
        )
        if selection.status is not InfrastructurePlacementSelectionStatus.SELECTED:
            return state, selection
        state = apply_infrastructure_greedy_selection(
            state,
            selection,
            infrastructure_type=infrastructure_type,
        )

    benefits = calculate_infrastructure_candidate_benefits(
        state,
        infrastructure_type=infrastructure_type,
    )
    return state, select_infrastructure_greedy_candidate(
        state,
        benefits,
        feasibility=_feasibility_for_benefits(benefits),
        iteration_index=policy.max_iterations,
        policy=policy,
    )


def test_greedy_known_optimum_covers_all_demand_with_two_candidates() -> None:
    demands = (
        _demand("block-a", 5.0),
        _demand("block-b", 4.0),
        _demand("block-c", 1.0),
    )
    candidates = (
        _candidate("candidate-c"),
        _candidate("candidate-b"),
        _candidate("candidate-a"),
    )
    state, stop = _run_greedy(
        demands,
        candidates,
        reachable_distances={
            ("block-a", "candidate-a"): 100.0,
            ("block-b", "candidate-a"): 200.0,
            ("block-b", "candidate-b"): 100.0,
            ("block-c", "candidate-b"): 200.0,
            ("block-c", "candidate-c"): 100.0,
        },
        infrastructure_type=_type(capacity=6.0),
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=3,
            max_iterations=3,
        ),
    )

    assert [
        item.candidate_ref.candidate_id for item in state.accepted_facilities
    ] == ["candidate-a", "candidate-b"]
    assert [item.remaining_demand for item in state.remaining_demand] == [
        0.0,
        0.0,
        0.0,
    ]
    assert stop.status is InfrastructurePlacementSelectionStatus.NO_POSITIVE_BENEFIT


def test_greedy_saturation_stops_before_redundant_candidate() -> None:
    demands = (_demand("block-a", 3.0),)
    candidates = (
        _candidate("candidate-b"),
        _candidate("candidate-a"),
    )
    state, stop = _run_greedy(
        demands,
        candidates,
        reachable_distances={
            ("block-a", "candidate-a"): 100.0,
            ("block-a", "candidate-b"): 100.0,
        },
        infrastructure_type=_type(capacity=10.0),
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=2,
            max_iterations=2,
        ),
    )

    assert [
        item.candidate_ref.candidate_id for item in state.accepted_facilities
    ] == ["candidate-a"]
    assert state.remaining_demand[0].remaining_demand == 0.0
    assert stop.status is InfrastructurePlacementSelectionStatus.NO_POSITIVE_BENEFIT


def test_greedy_equal_benefit_tie_is_stable_under_candidate_input_permutation() -> None:
    demands = (_demand("block-a", 5.0),)
    infrastructure_type = _type(capacity=5.0)
    policy = InfrastructureGreedyPlacementPolicy(
        max_facilities=1,
        max_iterations=1,
    )
    reachable = {
        ("block-a", "candidate-a"): 100.0,
        ("block-a", "candidate-b"): 100.0,
    }

    first_state, _ = _run_greedy(
        demands,
        (
            _candidate("candidate-b"),
            _candidate("candidate-a"),
        ),
        reachable_distances=reachable,
        infrastructure_type=infrastructure_type,
        policy=policy,
    )
    second_state, _ = _run_greedy(
        demands,
        (
            _candidate("candidate-a"),
            _candidate("candidate-b"),
        ),
        reachable_distances=reachable,
        infrastructure_type=infrastructure_type,
        policy=policy,
    )

    assert first_state == second_state
    assert first_state.accepted_facilities[0].candidate_ref == _candidate(
        "candidate-a"
    )


def test_greedy_no_feasible_candidate_stops_without_mutating_demand() -> None:
    demands = (
        _demand("block-a", 4.0),
        _demand("block-b", 2.0),
    )
    state, stop = _run_greedy(
        demands,
        (
            _candidate("candidate-b"),
            _candidate("candidate-a"),
        ),
        reachable_distances={},
        infrastructure_type=_type(capacity=5.0),
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=2,
            max_iterations=2,
        ),
    )

    assert state.accepted_facilities == ()
    assert [item.remaining_demand for item in state.remaining_demand] == [
        4.0,
        2.0,
    ]
    assert stop.status is InfrastructurePlacementSelectionStatus.NO_POSITIVE_BENEFIT
