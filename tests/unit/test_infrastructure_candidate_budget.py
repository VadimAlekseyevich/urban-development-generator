import pytest
from shapely.geometry import box

from core.urban_generator.domain import ConfigError
from core.urban_generator.infrastructure import (
    InfrastructureCandidateAccessibilityPolicy,
    InfrastructureCandidateSiteDiagnostics,
    InfrastructureCandidateSiteResult,
    InfrastructureCandidateSource,
    InfrastructureSiteCandidate,
)
from core.urban_generator.stages.infrastructure import _budgeted_sites
from core.urban_generator.zoning import ZoneClass


def _sites(count: int) -> InfrastructureCandidateSiteResult:
    candidates = []
    for index in range(count):
        geometry = box(index * 20, 0, index * 20 + 12, 12)
        candidates.append(InfrastructureSiteCandidate(
            candidate_id=f"candidate-{index:06d}",
            infrastructure_type_code="clinic.primary",
            source_kind=InfrastructureCandidateSource.BUILDING,
            source_id=f"building-{index:06d}",
            zone_class=ZoneClass.RESIDENTIAL,
            working_srid=32637,
            source_geometry=geometry,
            available_area_m2=144,
            anchor=geometry.centroid,
        ))
    return InfrastructureCandidateSiteResult(
        infrastructure_type_code="clinic.primary",
        working_srid=32637,
        candidates=tuple(candidates),
        diagnostics=InfrastructureCandidateSiteDiagnostics(
            block_input_count=0, parcel_input_count=0, building_input_count=count,
            candidate_count=count, skipped_unzoned_count=0, skipped_disallowed_zone_count=0,
        ),
    )


def test_candidate_budget_preserves_exhaustive_small_case() -> None:
    sites = _sites(12)
    assert _budgeted_sites(sites, 10, InfrastructureCandidateAccessibilityPolicy()) is sites


def test_candidate_budget_bounds_matrix_and_spreads_candidates() -> None:
    sites = _sites(100)
    policy = InfrastructureCandidateAccessibilityPolicy(max_results=100)
    result = _budgeted_sites(sites, 20, policy)
    assert result == _budgeted_sites(sites, 20, policy)
    assert len(result.candidates) == 5
    assert len(result.candidates) * 20 <= policy.max_results
    assert result.diagnostics.candidate_count == 5
    assert result.candidates[0].anchor.x < 400
    assert result.candidates[-1].anchor.x > 1600


def test_candidate_budget_rejects_impossible_single_candidate() -> None:
    with pytest.raises(ConfigError, match="even one candidate"):
        _budgeted_sites(_sites(10), 20, InfrastructureCandidateAccessibilityPolicy(max_results=10))


def test_candidate_budget_handles_millions_of_pairs() -> None:
    sites = _sites(2000)
    policy = InfrastructureCandidateAccessibilityPolicy()
    # Close to the 4.19/4.78 million pairs reported in the user's local worker logs.
    demand_count = 2400
    assert len(sites.candidates) * demand_count > policy.max_results
    result = _budgeted_sites(sites, demand_count, policy)
    assert len(result.candidates) == 250_000 // demand_count
    assert len(result.candidates) * demand_count <= 250_000
    assert result.candidates[0].anchor.x < 500
    assert result.candidates[-1].anchor.x > 39_500
    assert result == _budgeted_sites(sites, demand_count, policy)
