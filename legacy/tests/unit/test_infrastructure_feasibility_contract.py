from __future__ import annotations

import pytest

from core.urban_generator.infrastructure import (
    InfrastructureCandidateGeometryKind,
)
from core.urban_generator.infrastructure.feasibility import (
    InfrastructureFeasibilityError,
    InfrastructureFeasibilityRejectionReason,
    InfrastructureFeasibilityResult,
)

TYPE_CODE = "school.general"
WORKING_SRID = 3857


def test_feasible_site_result_has_no_rejections() -> None:
    result = InfrastructureFeasibilityResult(
        candidate_id="candidate-a",
        infrastructure_type_code=TYPE_CODE,
        working_srid=WORKING_SRID,
        geometry_kind=InfrastructureCandidateGeometryKind.SITE,
        proposed_capacity=500.0,
        is_feasible=True,
    )

    assert result.key == ("candidate-a", TYPE_CODE)
    assert result.proposed_capacity == 500.0
    assert result.rejection_reasons == ()


def test_rejections_are_canonical_and_unique() -> None:
    result = InfrastructureFeasibilityResult(
        candidate_id="candidate-a",
        infrastructure_type_code=TYPE_CODE,
        working_srid=WORKING_SRID,
        geometry_kind=InfrastructureCandidateGeometryKind.SITE,
        proposed_capacity=500.0,
        is_feasible=False,
        rejection_reasons=(
            InfrastructureFeasibilityRejectionReason.SITE_AREA_BELOW_MINIMUM,
            InfrastructureFeasibilityRejectionReason.CAPACITY_EXCEEDS_TYPE_CAPACITY,
            InfrastructureFeasibilityRejectionReason.SITE_AREA_BELOW_MINIMUM,
        ),
    )

    assert result.rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.CAPACITY_EXCEEDS_TYPE_CAPACITY,
        InfrastructureFeasibilityRejectionReason.SITE_AREA_BELOW_MINIMUM,
    )


@pytest.mark.parametrize(
    ("is_feasible", "reasons"),
    [
        (
            True,
            (
                InfrastructureFeasibilityRejectionReason.CAPACITY_EXCEEDS_TYPE_CAPACITY,
            ),
        ),
        (False, ()),
    ],
)
def test_feasible_flag_must_match_rejection_presence(
    is_feasible: bool,
    reasons: tuple[InfrastructureFeasibilityRejectionReason, ...],
) -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="is_feasible must be true exactly",
    ):
        InfrastructureFeasibilityResult(
            candidate_id="candidate-a",
            infrastructure_type_code=TYPE_CODE,
            working_srid=WORKING_SRID,
            geometry_kind=InfrastructureCandidateGeometryKind.SITE,
            proposed_capacity=500.0,
            is_feasible=is_feasible,
            rejection_reasons=reasons,
        )


def test_site_result_rejects_host_building_reason() -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="site feasibility result cannot use host-building",
    ):
        InfrastructureFeasibilityResult(
            candidate_id="candidate-a",
            infrastructure_type_code=TYPE_CODE,
            working_srid=WORKING_SRID,
            geometry_kind=InfrastructureCandidateGeometryKind.SITE,
            proposed_capacity=500.0,
            is_feasible=False,
            rejection_reasons=(
                InfrastructureFeasibilityRejectionReason.HOST_BUILDING_GEOMETRY_UNAVAILABLE,
            ),
        )


def test_host_building_result_rejects_site_reason() -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="host-building feasibility result cannot use site",
    ):
        InfrastructureFeasibilityResult(
            candidate_id="candidate-a",
            infrastructure_type_code=TYPE_CODE,
            working_srid=WORKING_SRID,
            geometry_kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
            proposed_capacity=500.0,
            is_feasible=False,
            rejection_reasons=(
                InfrastructureFeasibilityRejectionReason.SITE_AREA_BELOW_MINIMUM,
            ),
        )


def test_host_building_result_accepts_host_specific_rejections() -> None:
    result = InfrastructureFeasibilityResult(
        candidate_id="candidate-a",
        infrastructure_type_code=TYPE_CODE,
        working_srid=WORKING_SRID,
        geometry_kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
        proposed_capacity=500.0,
        is_feasible=False,
        rejection_reasons=(
            InfrastructureFeasibilityRejectionReason.HOST_BUILDING_GEOMETRY_UNAVAILABLE,
            InfrastructureFeasibilityRejectionReason.HOST_BUILDING_AREA_BELOW_MINIMUM,
        ),
    )

    assert result.rejection_reasons == (
        InfrastructureFeasibilityRejectionReason.HOST_BUILDING_AREA_BELOW_MINIMUM,
        InfrastructureFeasibilityRejectionReason.HOST_BUILDING_GEOMETRY_UNAVAILABLE,
    )


@pytest.mark.parametrize("capacity", [0.0, -1.0, float("inf"), float("nan")])
def test_result_requires_positive_finite_proposed_capacity(capacity: float) -> None:
    with pytest.raises(
        InfrastructureFeasibilityError,
        match="proposed_capacity must be a finite positive number",
    ):
        InfrastructureFeasibilityResult(
            candidate_id="candidate-a",
            infrastructure_type_code=TYPE_CODE,
            working_srid=WORKING_SRID,
            geometry_kind=InfrastructureCandidateGeometryKind.SITE,
            proposed_capacity=capacity,
            is_feasible=True,
        )
