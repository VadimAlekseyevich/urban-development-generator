from __future__ import annotations

import math

from shapely.geometry import box

from benchmarks.building_reference import (
    BuildingBenchmarkConfig,
    run_reference_building_benchmark,
)
from core.urban_generator.buildings import (
    BuildingPlacementConverger,
    BuildingPlacementProposal,
    BuildingPlacementTargets,
    BuildingSpacingPolicy,
)

WORKING_SRID = 3857


def _grid_proposals(count: int) -> tuple[BuildingPlacementProposal, ...]:
    columns = math.ceil(math.sqrt(count))
    return tuple(
        BuildingPlacementProposal(
            proposal_id=f"building:{index:04d}",
            source_id="parcel:property",
            geometry=box(
                (index % columns) * 3.0,
                (index // columns) * 3.0,
                (index % columns) * 3.0 + 2.0,
                (index // columns) * 3.0 + 2.0,
            ),
            working_srid=WORKING_SRID,
            planning_floor_area_multiplier=2.0,
        )
        for index in range(count)
    )


def test_building_pipeline_reference_is_deterministic_and_hits_targets() -> None:
    config = BuildingBenchmarkConfig(building_count=256)

    first = run_reference_building_benchmark(config)
    second = run_reference_building_benchmark(config)

    assert first.deterministic_digest == second.deterministic_digest
    assert first.accepted_count == config.building_count
    assert first.final_coverage_ratio == first.target_coverage_ratio
    assert first.final_far == first.target_far
    assert first.total_footprint_area_m2 > 0.0
    assert first.total_gfa_m2 > first.total_footprint_area_m2


def test_accepted_buildings_have_no_overlap_or_gap_violation() -> None:
    proposals = _grid_proposals(100)
    footprint_area = 4.0
    site_area = 100 * 9.0
    target_coverage = 100 * footprint_area / site_area

    result = BuildingPlacementConverger(
        working_srid=WORKING_SRID,
        spacing_policy=BuildingSpacingPolicy(minimum_gap_m=1.0),
        max_proposals=100,
        max_iterations=100,
        max_spacing_footprints=100,
    ).converge(
        proposals,
        targets=BuildingPlacementTargets(
            site_area_m2=site_area,
            target_coverage_ratio=target_coverage,
            target_far=target_coverage * 2.0,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
    )

    assert result.diagnostics.targets_met
    assert result.diagnostics.accepted_count == 100

    accepted = result.accepted
    for index, left in enumerate(accepted):
        for right in accepted[index + 1 :]:
            assert left.geometry.intersection(right.geometry).area == 0.0
            assert left.geometry.distance(right.geometry) >= 1.0
