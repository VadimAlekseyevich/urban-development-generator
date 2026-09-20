from __future__ import annotations

from dataclasses import replace

import pytest

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import NetworkNodeRef
from core.urban_generator.infrastructure import (
    BlockInfrastructureDemand,
    InfrastructureAcceptedFacility,
    InfrastructureAccessibilityBatchResult,
    InfrastructureAccessibilityDiagnostics,
    InfrastructureAccessibilityMode,
    InfrastructureAccessibilityResult,
    InfrastructureAccessibilityUnavailable,
    InfrastructureAccessibilityUnavailableReason,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructurePlacementDemandState,
    InfrastructurePlacementError,
    InfrastructureType,
    calculate_infrastructure_candidate_benefits,
    initialize_infrastructure_greedy_placement_state,
)
from core.urban_generator.zoning import ZoneClass

SNAPSHOT_ID = "roads:placement-benefit-v1"
TYPE_CODE = "school.general"


def _type(*, code: str = TYPE_CODE, capacity: float = 6.0) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=code,
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


def _accessibility() -> InfrastructureAccessibilityBatchResult:
    reachable = (
        _reachable("block-a", "candidate-a", 100.0),
        _reachable("block-b", "candidate-a", 300.0),
        _reachable("block-b", "candidate-b", 200.0),
    )
    unavailable = (
        _unavailable("block-a", "candidate-b"),
        _unavailable("block-a", "candidate-z"),
        _unavailable("block-b", "candidate-z"),
    )
    return InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.CANDIDATE_SITE,
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        reachable=tuple(sorted(reachable, key=lambda item: item.key)),
        unavailable=tuple(sorted(unavailable, key=lambda item: item.key)),
        diagnostics=InfrastructureAccessibilityDiagnostics(
            subject_count=6,
            reachable_count=3,
            unavailable_count=3,
            demand_unsnapped_count=0,
            facility_site_unsnapped_count=0,
            both_unsnapped_count=0,
            no_path_within_max_distance_count=3,
            no_snapped_facility_site_count=0,
        ),
    )


def _state():
    return initialize_infrastructure_greedy_placement_state(
        (
            _demand("block-b", 4.0),
            _demand("block-a", 5.0),
        ),
        (
            _candidate("candidate-z"),
            _candidate("candidate-b"),
            _candidate("candidate-a"),
        ),
        candidate_accessibility=_accessibility(),
    )


def test_candidate_benefit_uses_remaining_reachable_demand_and_capacity_cap() -> None:
    benefits = calculate_infrastructure_candidate_benefits(
        _state(),
        infrastructure_type=_type(capacity=6.0),
    )

    assert [item.candidate_ref.candidate_id for item in benefits] == [
        "candidate-a",
        "candidate-b",
        "candidate-z",
    ]
    assert [item.reachable_remaining_demand for item in benefits] == [
        9.0,
        4.0,
        0.0,
    ]
    assert [item.benefit for item in benefits] == [6.0, 4.0, 0.0]
    assert all(item.capacity == 6.0 for item in benefits)


def test_candidate_benefit_recalculates_from_current_state_without_touching_cache() -> None:
    state = _state()
    updated_state = replace(
        state,
        remaining_demand=(
            InfrastructurePlacementDemandState(
                demand_ref=_demand_ref("block-a"),
                initial_demand=5.0,
                remaining_demand=1.0,
            ),
            InfrastructurePlacementDemandState(
                demand_ref=_demand_ref("block-b"),
                initial_demand=4.0,
                remaining_demand=2.0,
            ),
        ),
    )

    original = calculate_infrastructure_candidate_benefits(
        state,
        infrastructure_type=_type(capacity=10.0),
    )
    updated = calculate_infrastructure_candidate_benefits(
        updated_state,
        infrastructure_type=_type(capacity=10.0),
    )

    assert [item.benefit for item in original] == [9.0, 4.0, 0.0]
    assert [item.benefit for item in updated] == [3.0, 2.0, 0.0]
    assert updated_state.coverage_cache is state.coverage_cache


def test_candidate_benefit_excludes_already_accepted_candidate() -> None:
    state = _state()
    accepted = replace(
        state,
        accepted_facilities=(
            InfrastructureAcceptedFacility(
                candidate_ref=_candidate("candidate-a"),
                acceptance_index=0,
            ),
        ),
    )

    benefits = calculate_infrastructure_candidate_benefits(
        accepted,
        infrastructure_type=_type(),
    )

    assert [item.candidate_ref.candidate_id for item in benefits] == [
        "candidate-b",
        "candidate-z",
    ]


def test_candidate_benefit_rejects_infrastructure_type_mismatch() -> None:
    with pytest.raises(
        InfrastructurePlacementError,
        match="code must match placement state",
    ):
        calculate_infrastructure_candidate_benefits(
            _state(),
            infrastructure_type=_type(code="school.other"),
        )
