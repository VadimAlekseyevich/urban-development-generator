from __future__ import annotations

import pytest
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
    InfrastructureGreedyPlacementState,
    InfrastructurePlacementError,
    InfrastructurePlacementSelectionStatus,
    InfrastructureType,
    select_infrastructure_greedy_candidate,
    validate_infrastructure_candidate_feasibility,
)
from core.urban_generator.zoning import ZoneClass

SNAPSHOT_ID = "roads:feasibility-greedy-v1"
TYPE_CODE = "school.general"
WORKING_SRID = 3857


def _type(
    *,
    capacity: float = 10.0,
    minimum_site_area_m2: float = 100.0,
) -> InfrastructureType:
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
        minimum_site_area_m2=minimum_site_area_m2,
        target_site_area_m2=max(minimum_site_area_m2, 200.0),
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _candidate_ref(candidate_id: str) -> InfrastructureCandidateRef:
    return InfrastructureCandidateRef(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
    )


def _site(
    candidate_id: str,
    *,
    area_m2: float,
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
        zone_id=f"zone-{candidate_id}",
        block_id=f"block-{candidate_id}",
    )


def _benefit(
    candidate_id: str,
    *,
    reachable: float,
    capacity: float,
) -> InfrastructureCandidateBenefit:
    return InfrastructureCandidateBenefit(
        candidate_ref=_candidate_ref(candidate_id),
        reachable_remaining_demand=reachable,
        capacity=capacity,
        benefit=min(reachable, capacity),
    )


def _state(
    candidate_ids: tuple[str, ...],
) -> InfrastructureGreedyPlacementState:
    candidate_refs = tuple(_candidate_ref(item) for item in candidate_ids)
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(),
        accepted_facilities=(),
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


def test_feasibility_filter_skips_higher_benefit_infeasible_candidate() -> None:
    infrastructure_type = _type()
    state = _state(("candidate-a", "candidate-b"))
    benefits = (
        _benefit("candidate-a", reachable=10.0, capacity=10.0),
        _benefit("candidate-b", reachable=8.0, capacity=10.0),
    )
    feasibility = (
        validate_infrastructure_candidate_feasibility(
            _site("candidate-a", area_m2=99.0),
            infrastructure_type=infrastructure_type,
            proposed_capacity=10.0,
        ),
        validate_infrastructure_candidate_feasibility(
            _site("candidate-b", area_m2=100.0),
            infrastructure_type=infrastructure_type,
            proposed_capacity=10.0,
        ),
    )

    selection = select_infrastructure_greedy_candidate(
        state,
        benefits,
        iteration_index=0,
        feasibility_results=feasibility,
    )

    assert selection.status is InfrastructurePlacementSelectionStatus.SELECTED
    assert selection.selected is not None
    assert selection.selected.candidate_ref == _candidate_ref("candidate-b")


def test_impossible_capacity_stops_when_no_candidate_is_feasible() -> None:
    infrastructure_type = _type(capacity=10.0)
    state = _state(("candidate-a",))
    benefits = (
        _benefit("candidate-a", reachable=5.0, capacity=11.0),
    )
    feasibility = (
        validate_infrastructure_candidate_feasibility(
            _site("candidate-a", area_m2=100.0),
            infrastructure_type=infrastructure_type,
            proposed_capacity=11.0,
        ),
    )

    selection = select_infrastructure_greedy_candidate(
        state,
        benefits,
        iteration_index=0,
        feasibility_results=feasibility,
    )

    assert selection.status is (
        InfrastructurePlacementSelectionStatus.NO_FEASIBLE_CANDIDATES
    )
    assert selection.selected is None


def test_exact_capacity_boundary_remains_selectable() -> None:
    infrastructure_type = _type(capacity=10.0)
    state = _state(("candidate-a",))
    benefits = (
        _benefit("candidate-a", reachable=20.0, capacity=10.0),
    )
    feasibility = (
        validate_infrastructure_candidate_feasibility(
            _site("candidate-a", area_m2=100.0),
            infrastructure_type=infrastructure_type,
            proposed_capacity=10.0,
        ),
    )

    selection = select_infrastructure_greedy_candidate(
        state,
        benefits,
        iteration_index=0,
        feasibility_results=feasibility,
    )

    assert selection.status is InfrastructurePlacementSelectionStatus.SELECTED
    assert selection.selected is not None
    assert selection.selected.capacity == 10.0


def test_feasibility_capacity_must_match_candidate_benefit_capacity() -> None:
    infrastructure_type = _type(capacity=10.0)
    state = _state(("candidate-a",))
    benefits = (
        _benefit("candidate-a", reachable=5.0, capacity=9.0),
    )
    feasibility = (
        validate_infrastructure_candidate_feasibility(
            _site("candidate-a", area_m2=100.0),
            infrastructure_type=infrastructure_type,
            proposed_capacity=10.0,
        ),
    )

    with pytest.raises(
        InfrastructurePlacementError,
        match="proposed capacity must match candidate benefit capacity",
    ):
        select_infrastructure_greedy_candidate(
            state,
            benefits,
            iteration_index=0,
            feasibility_results=feasibility,
        )
