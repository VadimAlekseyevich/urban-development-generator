from __future__ import annotations

from shapely.geometry import Point, box

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.infrastructure import (
    InfrastructureCandidateBenefit,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureCoverageCacheEntry,
    InfrastructureDemandModel,
    InfrastructureFeasibilityRejectionReason,
    InfrastructureGreedyPlacementPolicy,
    InfrastructureGreedyPlacementState,
    InfrastructurePlacementSelectionStatus,
    InfrastructureType,
    select_infrastructure_greedy_candidate,
    validate_infrastructure_candidate_feasibility,
)
from core.urban_generator.zoning import ZoneClass

TYPE_CODE = "school.general"
WORKING_SRID = 3857
SNAPSHOT_ID = "roads:feasibility-filter-v1"


def _type() -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=1.0,
        ),
        capacity=10.0,
        max_network_distance_m=1_500.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=100.0,
        target_site_area_m2=200.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _candidate(candidate_id: str) -> InfrastructureCandidateRef:
    return InfrastructureCandidateRef(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _geometry(
    candidate_id: str,
    *,
    area_m2: float = 100.0,
) -> InfrastructureCandidateGeometry:
    geometry = box(0.0, 0.0, area_m2, 1.0)
    return InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id=f"parcel-{candidate_id}",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(0.5, 0.5),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=geometry,
        site_area_m2=float(geometry.area),
    )


def _benefit(
    candidate_id: str,
    *,
    capacity: float,
    reachable: float,
) -> InfrastructureCandidateBenefit:
    return InfrastructureCandidateBenefit(
        candidate_ref=_candidate(candidate_id),
        reachable_remaining_demand=reachable,
        capacity=capacity,
        benefit=min(capacity, reachable),
    )


def _state() -> InfrastructureGreedyPlacementState:
    candidates = (
        _candidate("candidate-a"),
        _candidate("candidate-b"),
    )
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(),
        accepted_facilities=(),
        coverage_cache=tuple(
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                candidate_ref=candidate,
                accessibility=(),
            )
            for candidate in candidates
        ),
        candidate_order=candidates,
    )


def test_greedy_selection_skips_impossible_capacity_before_acceptance() -> None:
    infrastructure_type = _type()
    benefits = (
        _benefit("candidate-a", capacity=11.0, reachable=20.0),
        _benefit("candidate-b", capacity=10.0, reachable=10.0),
    )
    feasibility = (
        validate_infrastructure_candidate_feasibility(
            _geometry("candidate-a"),
            infrastructure_type=infrastructure_type,
            proposed_capacity=11.0,
        ),
        validate_infrastructure_candidate_feasibility(
            _geometry("candidate-b"),
            infrastructure_type=infrastructure_type,
            proposed_capacity=10.0,
        ),
    )

    decision = select_infrastructure_greedy_candidate(
        _state(),
        benefits,
        feasibility=feasibility,
        iteration_index=0,
        policy=InfrastructureGreedyPlacementPolicy(
            max_facilities=2,
            max_iterations=2,
        ),
    )

    assert feasibility[0].rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.CAPACITY_EXCEEDS_TYPE_CAPACITY,
    )
    assert feasibility[1].is_feasible is True
    assert decision.status is InfrastructurePlacementSelectionStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.candidate_ref == _candidate("candidate-b")
    assert decision.selected_feasibility == feasibility[1]


def test_greedy_selection_accepts_exact_capacity_boundary() -> None:
    infrastructure_type = _type()
    benefits = (
        _benefit("candidate-a", capacity=10.0, reachable=12.0),
        _benefit("candidate-b", capacity=10.0, reachable=8.0),
    )
    feasibility = tuple(
        validate_infrastructure_candidate_feasibility(
            _geometry(item.candidate_ref.candidate_id),
            infrastructure_type=infrastructure_type,
            proposed_capacity=item.capacity,
        )
        for item in benefits
    )

    decision = select_infrastructure_greedy_candidate(
        _state(),
        benefits,
        feasibility=feasibility,
        iteration_index=0,
    )

    assert all(item.is_feasible for item in feasibility)
    assert decision.status is InfrastructurePlacementSelectionStatus.SELECTED
    assert decision.selected is not None
    assert decision.selected.candidate_ref == _candidate("candidate-a")


def test_greedy_selection_stops_when_every_candidate_is_hard_infeasible() -> None:
    infrastructure_type = _type()
    benefits = (
        _benefit("candidate-a", capacity=10.0, reachable=10.0),
        _benefit("candidate-b", capacity=10.0, reachable=9.0),
    )
    feasibility = (
        validate_infrastructure_candidate_feasibility(
            _geometry("candidate-a", area_m2=99.0),
            infrastructure_type=infrastructure_type,
            proposed_capacity=10.0,
        ),
        validate_infrastructure_candidate_feasibility(
            _geometry("candidate-b", area_m2=99.0),
            infrastructure_type=infrastructure_type,
            proposed_capacity=10.0,
        ),
    )

    decision = select_infrastructure_greedy_candidate(
        _state(),
        benefits,
        feasibility=feasibility,
        iteration_index=0,
    )

    assert decision.status is (
        InfrastructurePlacementSelectionStatus.NO_FEASIBLE_CANDIDATE
    )
    assert decision.selected is None
    assert decision.selected_feasibility is None
