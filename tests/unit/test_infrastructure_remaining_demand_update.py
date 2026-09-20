from __future__ import annotations

from dataclasses import replace

import pytest

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import NetworkNodeRef
from core.urban_generator.infrastructure import (
    InfrastructureAccessibilityResult,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureCoverageCacheEntry,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureFeasibilityResult,
    InfrastructureGreedyPlacementPolicy,
    InfrastructureGreedyPlacementState,
    InfrastructurePlacementDemandState,
    InfrastructurePlacementError,
    InfrastructurePlacementSelection,
    InfrastructurePlacementSelectionStatus,
    InfrastructureType,
    apply_infrastructure_greedy_selection,
    calculate_infrastructure_candidate_benefits,
    select_infrastructure_greedy_candidate,
)
from core.urban_generator.zoning import ZoneClass

SNAPSHOT_ID = "roads:demand-update-v1"
TYPE_CODE = "school.general"


def _type(*, capacity: float = 6.0) -> InfrastructureType:
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


def _reachable(
    block_id: str,
    candidate_id: str,
    distance_m: float,
) -> InfrastructureAccessibilityResult:
    return InfrastructureAccessibilityResult(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        demand_ref=_demand_ref(block_id),
        facility_site_ref=_candidate(candidate_id),
        demand_node=NetworkNodeRef(node_id=f"demand-{block_id}"),
        facility_site_node=NetworkNodeRef(node_id=f"candidate-{candidate_id}"),
        max_network_distance_m=1_500.0,
        distance_m=distance_m,
    )


def _state() -> InfrastructureGreedyPlacementState:
    candidate_refs = (
        _candidate("candidate-a"),
        _candidate("candidate-b"),
    )
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(
            InfrastructurePlacementDemandState(
                demand_ref=_demand_ref("block-a"),
                initial_demand=5.0,
                remaining_demand=5.0,
            ),
            InfrastructurePlacementDemandState(
                demand_ref=_demand_ref("block-b"),
                initial_demand=4.0,
                remaining_demand=4.0,
            ),
        ),
        accepted_facilities=(),
        coverage_cache=(
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                candidate_ref=_candidate("candidate-a"),
                accessibility=(
                    _reachable("block-a", "candidate-a", 100.0),
                    _reachable("block-b", "candidate-a", 200.0),
                ),
            ),
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                candidate_ref=_candidate("candidate-b"),
                accessibility=(
                    _reachable("block-b", "candidate-b", 100.0),
                ),
            ),
        ),
        candidate_order=candidate_refs,
    )


def _select(
    state: InfrastructureGreedyPlacementState,
    *,
    iteration_index: int,
    infrastructure_type: InfrastructureType,
) -> InfrastructurePlacementSelection:
    benefits = calculate_infrastructure_candidate_benefits(
        state,
        infrastructure_type=infrastructure_type,
    )
    feasibility = tuple(
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
    return select_infrastructure_greedy_candidate(
        state,
        benefits,
        feasibility=feasibility,
        iteration_index=iteration_index,
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=2,
            max_iterations=2,
        ),
    )


def test_apply_selection_updates_only_current_reachable_demand_without_negative_values() -> None:
    infrastructure_type = _type(capacity=6.0)
    initial = _state()
    first_selection = _select(
        initial,
        iteration_index=0,
        infrastructure_type=infrastructure_type,
    )

    after_first = apply_infrastructure_greedy_selection(
        initial,
        first_selection,
        infrastructure_type=infrastructure_type,
    )

    assert [item.remaining_demand for item in after_first.remaining_demand] == [
        0.0,
        3.0,
    ]
    assert [item.initial_demand for item in after_first.remaining_demand] == [
        5.0,
        4.0,
    ]
    assert [
        item.candidate_ref.candidate_id
        for item in after_first.accepted_facilities
    ] == ["candidate-a"]
    assert after_first.coverage_cache is initial.coverage_cache

    second_selection = _select(
        after_first,
        iteration_index=1,
        infrastructure_type=infrastructure_type,
    )
    after_second = apply_infrastructure_greedy_selection(
        after_first,
        second_selection,
        infrastructure_type=infrastructure_type,
    )

    assert [item.remaining_demand for item in after_second.remaining_demand] == [
        0.0,
        0.0,
    ]
    assert all(
        item.remaining_demand >= 0.0 for item in after_second.remaining_demand
    )
    assert [
        item.candidate_ref.candidate_id
        for item in after_second.accepted_facilities
    ] == ["candidate-a", "candidate-b"]
    assert sum(item.initial_demand for item in after_second.remaining_demand) == 9.0
    assert sum(item.remaining_demand for item in after_second.remaining_demand) == 0.0


def test_apply_selection_rejects_stale_benefit_after_remaining_demand_changes() -> None:
    infrastructure_type = _type(capacity=6.0)
    state = _state()
    stale_selection = _select(
        state,
        iteration_index=0,
        infrastructure_type=infrastructure_type,
    )
    changed = replace(
        state,
        remaining_demand=(
            InfrastructurePlacementDemandState(
                demand_ref=_demand_ref("block-a"),
                initial_demand=5.0,
                remaining_demand=1.0,
            ),
            state.remaining_demand[1],
        ),
    )

    with pytest.raises(
        InfrastructurePlacementError,
        match="benefit is stale",
    ):
        apply_infrastructure_greedy_selection(
            changed,
            stale_selection,
            infrastructure_type=infrastructure_type,
        )


def test_apply_selection_rejects_same_candidate_twice() -> None:
    infrastructure_type = _type(capacity=6.0)
    state = _state()
    selection = _select(
        state,
        iteration_index=0,
        infrastructure_type=infrastructure_type,
    )
    updated = apply_infrastructure_greedy_selection(
        state,
        selection,
        infrastructure_type=infrastructure_type,
    )

    with pytest.raises(
        InfrastructurePlacementError,
        match="already been accepted",
    ):
        apply_infrastructure_greedy_selection(
            updated,
            selection,
            infrastructure_type=infrastructure_type,
        )


def test_apply_selection_rejects_non_selected_decision() -> None:
    with pytest.raises(
        InfrastructurePlacementError,
        match="only a selected placement decision",
    ):
        apply_infrastructure_greedy_selection(
            _state(),
            InfrastructurePlacementSelection(
                iteration_index=0,
                status=InfrastructurePlacementSelectionStatus.NO_POSITIVE_BENEFIT,
            ),
            infrastructure_type=_type(),
        )
