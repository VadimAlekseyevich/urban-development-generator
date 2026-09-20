from __future__ import annotations

import pytest

from core.urban_generator.infrastructure import (
    MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES,
    InfrastructureAcceptedFacility,
    InfrastructureCandidateBenefit,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateRef,
    InfrastructureCoverageCacheEntry,
    InfrastructureFeasibilityResult,
    InfrastructureGreedyPlacementPolicy,
    InfrastructureGreedyPlacementState,
    InfrastructurePlacementError,
    InfrastructurePlacementSelectionStatus,
    select_infrastructure_greedy_candidate,
)

SNAPSHOT_ID = "roads:selection-v1"
TYPE_CODE = "school.general"


def _candidate(candidate_id: str) -> InfrastructureCandidateRef:
    return InfrastructureCandidateRef(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _benefit(
    candidate_id: str,
    benefit: float,
    *,
    capacity: float = 10.0,
) -> InfrastructureCandidateBenefit:
    return InfrastructureCandidateBenefit(
        candidate_ref=_candidate(candidate_id),
        reachable_remaining_demand=benefit,
        capacity=capacity,
        benefit=benefit,
    )


def _feasibility(
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


def _state(
    *,
    accepted: tuple[str, ...] = (),
    candidates: tuple[str, ...] = ("candidate-a", "candidate-b", "candidate-z"),
) -> InfrastructureGreedyPlacementState:
    candidate_refs = tuple(_candidate(item) for item in candidates)
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(),
        accepted_facilities=tuple(
            InfrastructureAcceptedFacility(
                candidate_ref=_candidate(candidate_id),
                acceptance_index=index,
            )
            for index, candidate_id in enumerate(accepted)
        ),
        coverage_cache=tuple(
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                candidate_ref=candidate_ref,
                accessibility=(),
            )
            for candidate_ref in candidate_refs
        ),
        candidate_order=candidate_refs,
    )


def test_greedy_selection_breaks_equal_benefit_tie_by_candidate_order() -> None:
    benefits = (
        _benefit("candidate-a", 5.0),
        _benefit("candidate-b", 5.0),
        _benefit("candidate-z", 4.0),
    )
    decision = select_infrastructure_greedy_candidate(
        _state(),
        benefits,
        feasibility=_feasibility(benefits),
        iteration_index=0,
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=3,
            max_iterations=3,
        ),
    )

    assert decision.status is InfrastructurePlacementSelectionStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.candidate_ref == _candidate("candidate-a")
    assert decision.selected.benefit == 5.0


def test_greedy_selection_stops_at_facility_limit_before_selecting() -> None:
    benefits = (
        _benefit("candidate-b", 5.0),
        _benefit("candidate-z", 4.0),
    )
    decision = select_infrastructure_greedy_candidate(
        _state(accepted=("candidate-a",)),
        benefits,
        feasibility=_feasibility(benefits),
        iteration_index=1,
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=1,
            max_iterations=5,
        ),
    )

    assert decision.status is (
        InfrastructurePlacementSelectionStatus.FACILITY_LIMIT_REACHED
    )
    assert decision.selected is None


def test_greedy_selection_stops_at_zero_based_iteration_limit() -> None:
    benefits = (
        _benefit("candidate-a", 5.0),
        _benefit("candidate-b", 4.0),
        _benefit("candidate-z", 3.0),
    )
    decision = select_infrastructure_greedy_candidate(
        _state(),
        benefits,
        feasibility=_feasibility(benefits),
        iteration_index=2,
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=3,
            max_iterations=2,
        ),
    )

    assert decision.status is (
        InfrastructurePlacementSelectionStatus.ITERATION_LIMIT_REACHED
    )
    assert decision.selected is None


def test_greedy_selection_stops_when_all_remaining_benefits_are_zero() -> None:
    benefits = (
        _benefit("candidate-a", 0.0),
        _benefit("candidate-b", 0.0),
        _benefit("candidate-z", 0.0),
    )
    decision = select_infrastructure_greedy_candidate(
        _state(),
        benefits,
        feasibility=_feasibility(benefits),
        iteration_index=0,
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=3,
            max_iterations=3,
        ),
    )

    assert decision.status is (
        InfrastructurePlacementSelectionStatus.NO_POSITIVE_BENEFIT
    )
    assert decision.selected is None


def test_greedy_selection_reports_no_unaccepted_candidates() -> None:
    decision = select_infrastructure_greedy_candidate(
        _state(
            accepted=("candidate-a",),
            candidates=("candidate-a",),
        ),
        (),
        feasibility=(),
        iteration_index=1,
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=2,
            max_iterations=3,
        ),
    )

    assert decision.status is InfrastructurePlacementSelectionStatus.NO_CANDIDATES
    assert decision.selected is None


def test_greedy_selection_rejects_reordered_or_incomplete_benefits() -> None:
    with pytest.raises(
        InfrastructurePlacementError,
        match="every unaccepted candidate in candidate_order",
    ):
        select_infrastructure_greedy_candidate(
            _state(),
            (
                _benefit("candidate-b", 5.0),
                _benefit("candidate-a", 5.0),
            ),
            feasibility=(
                InfrastructureFeasibilityResult(
                    candidate_id="candidate-a",
                    infrastructure_type_code=TYPE_CODE,
                    working_srid=3857,
                    geometry_kind=InfrastructureCandidateGeometryKind.SITE,
                    proposed_capacity=5.0,
                    is_feasible=True,
                ),
                InfrastructureFeasibilityResult(
                    candidate_id="candidate-b",
                    infrastructure_type_code=TYPE_CODE,
                    working_srid=3857,
                    geometry_kind=InfrastructureCandidateGeometryKind.SITE,
                    proposed_capacity=5.0,
                    is_feasible=True,
                ),
            ),
            iteration_index=0,
        )


def test_greedy_placement_policy_rejects_hard_limit_overflow() -> None:
    with pytest.raises(
        InfrastructurePlacementError,
        match="max_facilities exceeds placement hard limit",
    ):
        InfrastructureGreedyPlacementPolicy(
            max_facilities=MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES + 1,
            max_iterations=1,
        )
