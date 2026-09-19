from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from core.urban_generator.buildings import (
    BuildingPlacementBaseline,
    BuildingPlacementConvergenceError,
    BuildingPlacementConvergenceStatus,
    BuildingPlacementConverger,
    BuildingPlacementProposal,
    BuildingPlacementTargets,
    BuildingSpacingPolicy,
    PlacedBuildingFootprint,
)

WORKING_SRID = 3857


def _proposal(
    proposal_id: str,
    geometry,
    *,
    multiplier: float = 1.0,
    priority: float = 0.0,
    working_srid: int = WORKING_SRID,
) -> BuildingPlacementProposal:
    return BuildingPlacementProposal(
        proposal_id=proposal_id,
        source_id="parcel:test",
        geometry=geometry,
        working_srid=working_srid,
        planning_floor_area_multiplier=multiplier,
        priority=priority,
    )


def _existing(
    building_id: str,
    geometry,
    *,
    working_srid: int = WORKING_SRID,
) -> PlacedBuildingFootprint:
    return PlacedBuildingFootprint(
        building_id=building_id,
        geometry=geometry,
        working_srid=working_srid,
    )


def test_converges_exact_coverage_and_far_targets() -> None:
    proposals = tuple(
        _proposal(
            f"proposal:{index}",
            box(index * 20, 0, index * 20 + 10, 5),
            multiplier=2.0,
        )
        for index in range(4)
    )
    converger = BuildingPlacementConverger(working_srid=WORKING_SRID)

    result = converger.converge(
        proposals,
        targets=BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=0.20,
            target_far=0.40,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
    )

    assert result.diagnostics.status is BuildingPlacementConvergenceStatus.CONVERGED
    assert result.diagnostics.targets_met is True
    assert result.diagnostics.iteration_count == 4
    assert result.diagnostics.accepted_count == 4
    assert result.diagnostics.final_coverage_ratio == pytest.approx(0.20)
    assert result.diagnostics.final_far == pytest.approx(0.40)
    assert result.diagnostics.unmet_coverage_ratio == pytest.approx(0.0)
    assert result.diagnostics.unmet_far == pytest.approx(0.0)


def test_spacing_rejection_includes_existing_and_newly_accepted_buildings() -> None:
    existing = (
        _existing("building:fixed", box(0, 0, 10, 10)),
    )
    proposals = (
        _proposal(
            "proposal:bad-fixed",
            box(5, 0, 15, 10),
            priority=4.0,
        ),
        _proposal(
            "proposal:one",
            box(20, 0, 30, 5),
            priority=3.0,
        ),
        _proposal(
            "proposal:bad-new",
            box(25, 0, 35, 5),
            priority=2.0,
        ),
        _proposal(
            "proposal:two",
            box(40, 0, 50, 5),
            priority=1.0,
        ),
    )
    converger = BuildingPlacementConverger(working_srid=WORKING_SRID)

    result = converger.converge(
        proposals,
        targets=BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=0.20,
            target_far=0.20,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
        baseline=BuildingPlacementBaseline(
            coverage_area_m2=100.0,
            planning_floor_area_m2=100.0,
        ),
        existing_footprints=existing,
    )

    assert result.diagnostics.status is BuildingPlacementConvergenceStatus.CONVERGED
    assert tuple(item.proposal_id for item in result.accepted) == (
        "proposal:one",
        "proposal:two",
    )
    assert result.diagnostics.spacing_rejected_count == 2
    assert result.diagnostics.final_coverage_ratio == pytest.approx(0.20)
    assert result.diagnostics.final_far == pytest.approx(0.20)


def test_target_window_rejects_overshoot_then_accepts_compatible_proposal() -> None:
    converger = BuildingPlacementConverger(working_srid=WORKING_SRID)
    proposals = (
        _proposal(
            "proposal:overshoot",
            box(0, 0, 10, 10),
            multiplier=3.0,
            priority=2.0,
        ),
        _proposal(
            "proposal:fit",
            box(20, 0, 30, 10),
            multiplier=2.0,
            priority=1.0,
        ),
    )

    result = converger.converge(
        proposals,
        targets=BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=0.10,
            target_far=0.20,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
    )

    assert result.diagnostics.status is BuildingPlacementConvergenceStatus.CONVERGED
    assert tuple(item.proposal_id for item in result.accepted) == ("proposal:fit",)
    assert result.diagnostics.target_window_rejected_count == 1
    assert result.diagnostics.iteration_count == 2


def test_priority_then_id_order_is_deterministic() -> None:
    high = _proposal(
        "proposal:z-high",
        box(0, 0, 5, 5),
        priority=10.0,
    )
    low = _proposal(
        "proposal:a-low",
        box(20, 0, 25, 5),
        priority=1.0,
    )
    targets = BuildingPlacementTargets(
        site_area_m2=100.0,
        target_coverage_ratio=0.25,
        target_far=0.25,
        coverage_tolerance=0.0,
        far_tolerance=0.0,
    )
    converger = BuildingPlacementConverger(working_srid=WORKING_SRID)

    first = converger.converge((low, high), targets=targets)
    second = converger.converge((high, low), targets=targets)

    assert first == second
    assert tuple(item.proposal_id for item in first.accepted) == (
        "proposal:z-high",
    )


def test_tolerance_window_can_converge_without_exact_target() -> None:
    converger = BuildingPlacementConverger(working_srid=WORKING_SRID)
    proposal = _proposal(
        "proposal:tolerated",
        box(0, 0, 19, 10),
        multiplier=2.0,
    )

    result = converger.converge(
        (proposal,),
        targets=BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=0.20,
            target_far=0.40,
            coverage_tolerance=0.02,
            far_tolerance=0.03,
        ),
    )

    assert result.diagnostics.status is BuildingPlacementConvergenceStatus.CONVERGED
    assert result.diagnostics.final_coverage_ratio == pytest.approx(0.19)
    assert result.diagnostics.final_far == pytest.approx(0.38)


def test_candidates_exhausted_reports_unmet_targets() -> None:
    converger = BuildingPlacementConverger(working_srid=WORKING_SRID)

    result = converger.converge(
        (
            _proposal(
                "proposal:only",
                box(0, 0, 10, 5),
                multiplier=2.0,
            ),
        ),
        targets=BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=0.20,
            target_far=0.40,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
    )

    assert (
        result.diagnostics.status
        is BuildingPlacementConvergenceStatus.CANDIDATES_EXHAUSTED
    )
    assert result.diagnostics.targets_met is False
    assert result.diagnostics.final_coverage_ratio == pytest.approx(0.05)
    assert result.diagnostics.final_far == pytest.approx(0.10)
    assert result.diagnostics.unmet_coverage_ratio == pytest.approx(0.15)
    assert result.diagnostics.unmet_far == pytest.approx(0.30)


def test_max_iterations_is_a_hard_bound() -> None:
    proposals = tuple(
        _proposal(
            f"proposal:{index}",
            box(index * 20, 0, index * 20 + 10, 5),
            multiplier=2.0,
        )
        for index in range(4)
    )
    converger = BuildingPlacementConverger(
        working_srid=WORKING_SRID,
        max_iterations=2,
    )

    result = converger.converge(
        proposals,
        targets=BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=0.20,
            target_far=0.40,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
    )

    assert (
        result.diagnostics.status
        is BuildingPlacementConvergenceStatus.MAX_ITERATIONS
    )
    assert result.diagnostics.iteration_count == 2
    assert result.diagnostics.accepted_count == 2
    assert result.diagnostics.final_coverage_ratio == pytest.approx(0.10)
    assert result.diagnostics.final_far == pytest.approx(0.20)


def test_already_satisfied_baseline_short_circuits_without_iterations() -> None:
    converger = BuildingPlacementConverger(working_srid=WORKING_SRID)

    result = converger.converge(
        (
            _proposal(
                "proposal:unused",
                box(0, 0, 10, 10),
                multiplier=2.0,
            ),
        ),
        targets=BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=0.20,
            target_far=0.40,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
        baseline=BuildingPlacementBaseline(
            coverage_area_m2=200.0,
            planning_floor_area_m2=400.0,
        ),
    )

    assert result.diagnostics.status is BuildingPlacementConvergenceStatus.CONVERGED
    assert result.diagnostics.iteration_count == 0
    assert result.accepted == ()


def test_positive_spacing_gap_is_applied_by_convergence_loop() -> None:
    converger = BuildingPlacementConverger(
        working_srid=WORKING_SRID,
        spacing_policy=BuildingSpacingPolicy(minimum_gap_m=5.0),
    )
    proposals = (
        _proposal("proposal:one", box(0, 0, 10, 5)),
        _proposal("proposal:too-close", box(12, 0, 22, 5)),
        _proposal("proposal:two", box(30, 0, 40, 5)),
    )

    result = converger.converge(
        proposals,
        targets=BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=0.10,
            target_far=0.10,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
    )

    assert tuple(item.proposal_id for item in result.accepted) == (
        "proposal:one",
        "proposal:two",
    )
    assert result.diagnostics.spacing_rejected_count == 1


def test_contract_rejects_invalid_targets_geometry_crs_ids_and_bounds() -> None:
    with pytest.raises(BuildingPlacementConvergenceError, match="<= 1.0"):
        BuildingPlacementTargets(
            site_area_m2=1000.0,
            target_coverage_ratio=1.1,
            target_far=1.0,
        )
    with pytest.raises(BuildingPlacementConvergenceError, match="greater than zero"):
        BuildingPlacementProposal(
            proposal_id="proposal:bad",
            source_id="parcel:test",
            geometry=box(0, 0, 10, 10),
            working_srid=WORKING_SRID,
            planning_floor_area_multiplier=0.0,
        )
    with pytest.raises(BuildingPlacementConvergenceError, match="Polygon"):
        _proposal("proposal:point", Point(0, 0))

    converger = BuildingPlacementConverger(
        working_srid=WORKING_SRID,
        max_proposals=1,
    )
    targets = BuildingPlacementTargets(
        site_area_m2=1000.0,
        target_coverage_ratio=0.2,
        target_far=0.4,
    )
    proposal = _proposal("proposal:one", box(0, 0, 10, 10))

    with pytest.raises(BuildingPlacementConvergenceError, match="proposal limit"):
        converger.converge((proposal, proposal), targets=targets)
    with pytest.raises(BuildingPlacementConvergenceError, match="working_srid"):
        converger.converge(
            (
                _proposal(
                    "proposal:wrong-crs",
                    box(0, 0, 10, 10),
                    working_srid=32637,
                ),
            ),
            targets=targets,
        )
    with pytest.raises(BuildingPlacementConvergenceError, match="cannot exceed"):
        converger.converge(
            (),
            targets=targets,
            baseline=BuildingPlacementBaseline(coverage_area_m2=1001.0),
        )
    with pytest.raises(ValueError, match="not projected"):
        BuildingPlacementConverger(working_srid=4326)


def test_proposal_ids_cannot_collide_with_existing_building_ids() -> None:
    converger = BuildingPlacementConverger(working_srid=WORKING_SRID)
    proposal = _proposal("building:same", box(20, 0, 30, 10))

    with pytest.raises(BuildingPlacementConvergenceError, match="collide"):
        converger.converge(
            (proposal,),
            targets=BuildingPlacementTargets(
                site_area_m2=1000.0,
                target_coverage_ratio=0.2,
                target_far=0.2,
            ),
            existing_footprints=(
                _existing("building:same", box(0, 0, 10, 10)),
            ),
        )
