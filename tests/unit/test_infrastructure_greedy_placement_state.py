from __future__ import annotations

from dataclasses import replace

import pytest

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
    InfrastructureAcceptedFacility,
    InfrastructureCandidateRef,
    InfrastructureCategory,
    InfrastructureCoverageCacheEntry,
    InfrastructureDemandRef,
    InfrastructureGreedyPlacementState,
    InfrastructurePlacementError,
    InfrastructureType,
    ZoneClass,
    initialize_infrastructure_greedy_placement_state,
)

SNAPSHOT_ID = "roads:placement-v1"
TYPE_CODE = "school.general"


def _demand(block_id: str, unmet_demand: float) -> BlockInfrastructureDemand:
    return BlockInfrastructureDemand(
        block_id=block_id,
        zone_id=f"zone-{block_id}",
        zone_class=ZoneClass.PUBLIC,
        infrastructure_type_code=TYPE_CODE,
        infrastructure_category=InfrastructureCategory.EDUCATION,
        demographic_signal=DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
        source_signal_value=unmet_demand,
        demand_rate=1.0,
        gross_demand=unmet_demand,
        served_demand=0.0,
        unmet_demand=unmet_demand,
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
        facility_site_node=NetworkNodeRef(node_id=f"site-{candidate_id}"),
        max_network_distance_m=1_500.0,
        distance_m=distance_m,
    )


def _unavailable(
    block_id: str,
    candidate_id: str,
) -> InfrastructureAccessibilityUnavailable:
    return InfrastructureAccessibilityUnavailable(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        demand_ref=_demand_ref(block_id),
        facility_site_ref=_candidate(candidate_id),
        max_network_distance_m=1_500.0,
        reason=InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE,
    )


def _batch(
    *,
    reachable: tuple[InfrastructureAccessibilityResult, ...],
    unavailable: tuple[InfrastructureAccessibilityUnavailable, ...],
) -> InfrastructureAccessibilityBatchResult:
    reachable = tuple(sorted(reachable, key=lambda item: item.key))
    unavailable = tuple(sorted(unavailable, key=lambda item: item.key))
    return InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.CANDIDATE_SITE,
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        reachable=reachable,
        unavailable=unavailable,
        diagnostics=InfrastructureAccessibilityDiagnostics(
            subject_count=len(reachable) + len(unavailable),
            reachable_count=len(reachable),
            unavailable_count=len(unavailable),
            demand_unsnapped_count=0,
            facility_site_unsnapped_count=0,
            both_unsnapped_count=0,
            no_path_within_max_distance_count=len(unavailable),
            no_snapped_facility_site_count=0,
        ),
    )


def _complete_batch() -> InfrastructureAccessibilityBatchResult:
    return _batch(
        reachable=(
            _reachable("block-a", "candidate-a", 100.0),
            _reachable("block-b", "candidate-z", 200.0),
        ),
        unavailable=(
            _unavailable("block-a", "candidate-z"),
            _unavailable("block-b", "candidate-a"),
        ),
    )


def test_placement_state_initialization_is_canonical_under_input_permutation() -> None:
    first = initialize_infrastructure_greedy_placement_state(
        (
            _demand("block-b", 3.0),
            _demand("block-a", 5.0),
        ),
        (
            _candidate("candidate-z"),
            _candidate("candidate-a"),
        ),
        candidate_accessibility=_complete_batch(),
    )
    second = initialize_infrastructure_greedy_placement_state(
        (
            _demand("block-a", 5.0),
            _demand("block-b", 3.0),
        ),
        (
            _candidate("candidate-a"),
            _candidate("candidate-z"),
        ),
        candidate_accessibility=_complete_batch(),
    )

    assert first == second
    assert [item.demand_ref.block_id for item in first.remaining_demand] == [
        "block-a",
        "block-b",
    ]
    assert [item.initial_demand for item in first.remaining_demand] == [5.0, 3.0]
    assert [item.remaining_demand for item in first.remaining_demand] == [5.0, 3.0]
    assert first.accepted_facilities == ()
    assert [item.candidate_id for item in first.candidate_order] == [
        "candidate-a",
        "candidate-z",
    ]
    assert [
        item.candidate_ref.candidate_id for item in first.coverage_cache
    ] == [
        "candidate-a",
        "candidate-z",
    ]
    assert [
        [row.demand_ref.block_id for row in item.accessibility]
        for item in first.coverage_cache
    ] == [["block-a"], ["block-b"]]


def test_placement_state_keeps_empty_cache_entry_for_unreachable_candidate() -> None:
    batch = _batch(
        reachable=(
            _reachable("block-a", "candidate-a", 100.0),
        ),
        unavailable=(
            _unavailable("block-a", "candidate-z"),
        ),
    )

    state = initialize_infrastructure_greedy_placement_state(
        (_demand("block-a", 5.0),),
        (
            _candidate("candidate-z"),
            _candidate("candidate-a"),
        ),
        candidate_accessibility=batch,
    )

    assert [len(item.accessibility) for item in state.coverage_cache] == [1, 0]
    assert state.coverage_cache[1].candidate_ref.candidate_id == "candidate-z"


def test_placement_state_rejects_incomplete_candidate_demand_matrix() -> None:
    incomplete = _batch(
        reachable=(
            _reachable("block-a", "candidate-a", 100.0),
        ),
        unavailable=(
            _unavailable("block-b", "candidate-a"),
            _unavailable("block-a", "candidate-z"),
        ),
    )

    with pytest.raises(
        InfrastructurePlacementError,
        match="complete candidate-demand matrix",
    ):
        initialize_infrastructure_greedy_placement_state(
            (
                _demand("block-a", 5.0),
                _demand("block-b", 3.0),
            ),
            (
                _candidate("candidate-a"),
                _candidate("candidate-z"),
            ),
            candidate_accessibility=incomplete,
        )


def test_placement_state_rejects_accepted_facility_outside_candidate_order() -> None:
    state = initialize_infrastructure_greedy_placement_state(
        (
            _demand("block-a", 5.0),
            _demand("block-b", 3.0),
        ),
        (
            _candidate("candidate-a"),
            _candidate("candidate-z"),
        ),
        candidate_accessibility=_complete_batch(),
    )

    with pytest.raises(
        InfrastructurePlacementError,
        match="accepted facility must reference candidate_order",
    ):
        replace(
            state,
            accepted_facilities=(
                InfrastructureAcceptedFacility(
                    candidate_ref=_candidate("candidate-missing"),
                    acceptance_index=0,
                ),
            ),
        )


def test_placement_state_rejects_cache_rows_for_unknown_demand() -> None:
    state = initialize_infrastructure_greedy_placement_state(
        (
            _demand("block-a", 5.0),
            _demand("block-b", 3.0),
        ),
        (
            _candidate("candidate-a"),
            _candidate("candidate-z"),
        ),
        candidate_accessibility=_complete_batch(),
    )
    bad_cache = (
        InfrastructureCoverageCacheEntry(
            snapshot_id=SNAPSHOT_ID,
            infrastructure_type_code=TYPE_CODE,
            candidate_ref=_candidate("candidate-a"),
            accessibility=(
                _reachable("block-missing", "candidate-a", 50.0),
            ),
        ),
        state.coverage_cache[1],
    )

    with pytest.raises(
        InfrastructurePlacementError,
        match="references demand outside placement state",
    ):
        InfrastructureGreedyPlacementState(
            snapshot_id=state.snapshot_id,
            infrastructure_type_code=state.infrastructure_type_code,
            remaining_demand=state.remaining_demand,
            accepted_facilities=(),
            coverage_cache=bad_cache,
            candidate_order=state.candidate_order,
        )
