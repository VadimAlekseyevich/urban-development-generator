from __future__ import annotations

import pytest

from core.urban_generator.domain import NetworkNodeRef
from core.urban_generator.infrastructure import (
    ExistingInfrastructureFacilityRef,
    InfrastructureAccessibilityError,
    InfrastructureAccessibilityQuery,
    InfrastructureAccessibilityResult,
    InfrastructureCandidateRef,
    InfrastructureDemandRef,
)


TYPE_CODE = "school.general"


def _demand_ref(*, type_code: str = TYPE_CODE) -> InfrastructureDemandRef:
    return InfrastructureDemandRef(
        block_id="block-1",
        infrastructure_type_code=type_code,
    )


def _facility_ref(
    *,
    facility_id: str = "site-1",
    type_code: str = TYPE_CODE,
) -> ExistingInfrastructureFacilityRef:
    return ExistingInfrastructureFacilityRef(
        facility_id=facility_id,
        infrastructure_type_code=type_code,
    )


def _candidate_ref(
    *,
    candidate_id: str = "site-1",
    type_code: str = TYPE_CODE,
) -> InfrastructureCandidateRef:
    return InfrastructureCandidateRef(
        candidate_id=candidate_id,
        infrastructure_type_code=type_code,
    )


def test_accessibility_query_key_keeps_existing_and_candidate_families_distinct() -> None:
    common = {
        "snapshot_id": "roads:v1",
        "infrastructure_type_code": TYPE_CODE,
        "demand_ref": _demand_ref(),
        "demand_node": NetworkNodeRef(node_id="demand-node"),
        "facility_site_node": NetworkNodeRef(node_id="site-node"),
        "max_network_distance_m": 1500.0,
    }

    existing = InfrastructureAccessibilityQuery(
        facility_site_ref=_facility_ref(),
        **common,
    )
    candidate = InfrastructureAccessibilityQuery(
        facility_site_ref=_candidate_ref(),
        **common,
    )

    assert existing.key == (
        TYPE_CODE,
        "block-1",
        "existing_facility",
        "site-1",
    )
    assert candidate.key == (
        TYPE_CODE,
        "block-1",
        "candidate_site",
        "site-1",
    )
    assert existing.key != candidate.key


@pytest.mark.parametrize(
    ("demand_ref", "facility_site_ref", "message"),
    [
        (
            _demand_ref(type_code="clinic.general"),
            _facility_ref(),
            "demand_ref infrastructure type",
        ),
        (
            _demand_ref(),
            _candidate_ref(type_code="clinic.general"),
            "facility_site_ref infrastructure type",
        ),
    ],
)
def test_accessibility_query_rejects_cross_type_refs(
    demand_ref: InfrastructureDemandRef,
    facility_site_ref: ExistingInfrastructureFacilityRef | InfrastructureCandidateRef,
    message: str,
) -> None:
    with pytest.raises(InfrastructureAccessibilityError, match=message):
        InfrastructureAccessibilityQuery(
            snapshot_id="roads:v1",
            infrastructure_type_code=TYPE_CODE,
            demand_ref=demand_ref,
            facility_site_ref=facility_site_ref,
            demand_node=NetworkNodeRef(node_id="demand-node"),
            facility_site_node=NetworkNodeRef(node_id="site-node"),
            max_network_distance_m=1500.0,
        )


@pytest.mark.parametrize(
    "max_distance_m",
    [0.0, -1.0, float("inf"), float("nan")],
)
def test_accessibility_query_requires_positive_finite_service_cutoff(
    max_distance_m: float,
) -> None:
    with pytest.raises(InfrastructureAccessibilityError):
        InfrastructureAccessibilityQuery(
            snapshot_id="roads:v1",
            infrastructure_type_code=TYPE_CODE,
            demand_ref=_demand_ref(),
            facility_site_ref=_facility_ref(),
            demand_node=NetworkNodeRef(node_id="demand-node"),
            facility_site_node=NetworkNodeRef(node_id="site-node"),
            max_network_distance_m=max_distance_m,
        )


def test_accessibility_result_preserves_query_identity_and_network_provenance() -> None:
    result = InfrastructureAccessibilityResult(
        snapshot_id="roads:v1",
        infrastructure_type_code=TYPE_CODE,
        demand_ref=_demand_ref(),
        facility_site_ref=_candidate_ref(candidate_id="candidate-7"),
        demand_node=NetworkNodeRef(node_id="demand-node"),
        facility_site_node=NetworkNodeRef(node_id="candidate-node"),
        max_network_distance_m=1500.0,
        distance_m=725.5,
    )

    assert result.key == (
        TYPE_CODE,
        "block-1",
        "candidate_site",
        "candidate-7",
    )
    assert result.snapshot_id == "roads:v1"
    assert result.demand_node == NetworkNodeRef(node_id="demand-node")
    assert result.facility_site_node == NetworkNodeRef(node_id="candidate-node")
    assert result.distance_m == pytest.approx(725.5)


def test_accessibility_result_rejects_distance_beyond_query_cutoff() -> None:
    with pytest.raises(
        InfrastructureAccessibilityError,
        match="must not exceed",
    ):
        InfrastructureAccessibilityResult(
            snapshot_id="roads:v1",
            infrastructure_type_code=TYPE_CODE,
            demand_ref=_demand_ref(),
            facility_site_ref=_facility_ref(),
            demand_node=NetworkNodeRef(node_id="demand-node"),
            facility_site_node=NetworkNodeRef(node_id="facility-node"),
            max_network_distance_m=1000.0,
            distance_m=1000.1,
        )


@pytest.mark.parametrize(
    "distance_m",
    [-1.0, float("inf"), float("nan")],
)
def test_accessibility_result_distance_is_finite_and_non_negative(
    distance_m: float,
) -> None:
    with pytest.raises(
        InfrastructureAccessibilityError,
        match="finite non-negative",
    ):
        InfrastructureAccessibilityResult(
            snapshot_id="roads:v1",
            infrastructure_type_code=TYPE_CODE,
            demand_ref=_demand_ref(),
            facility_site_ref=_facility_ref(),
            demand_node=NetworkNodeRef(node_id="demand-node"),
            facility_site_node=NetworkNodeRef(node_id="facility-node"),
            max_network_distance_m=1000.0,
            distance_m=distance_m,
        )
