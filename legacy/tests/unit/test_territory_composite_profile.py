"""S14-T07 composite area profiles: deterministic scale, hard budgets, evidence schema."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from benchmarks.territory_composite_profile import (
    PROFILE_SCHEMA,
    _assert_work_budgets,
    _machine,
    workload_for,
)


@pytest.mark.parametrize(
    ("area", "side", "road_grid", "raster_side", "buildings", "subjects"),
    (
        (25, 5_000, 21, 500, 1_000, 768),
        (100, 10_000, 41, 1_000, 3_000, 3_072),
    ),
)
def test_shared_metric_territory_and_hard_operation_budgets(
    area: int, side: int, road_grid: int, raster_side: int,
    buildings: int, subjects: int,
) -> None:
    workload = workload_for(area)
    assert workload.area_km2 == area
    assert workload.side_m == side
    assert workload.side_m**2 == area * 1_000_000
    assert workload.raster_dimension == raster_side
    assert workload.road_grid_size == road_grid
    assert workload.building_count == buildings
    assert workload.budget.max_raster_cells == raster_side**2
    assert workload.budget.max_road_candidate_pairs == road_grid**2
    assert workload.budget.max_building_proposals == buildings
    assert workload.budget.max_infrastructure_subjects == subjects
    assert workload.budget.max_routing_calls == (
        workload.infrastructure_candidates
        * ((workload.infrastructure_demands + 15) // 16)
    )
    assert workload.budget.advisory_total_wall_ms > 0
    assert workload.budget.advisory_process_peak_rss_mib > 0
    assert asdict(workload)["budget"]["max_raster_cells"] == raster_side**2


@pytest.mark.parametrize("bad_area", (0, -1, 1, 26, 50, 101, True, 25.0, "25"))
def test_unsupported_area_rejected_before_work(bad_area: object) -> None:
    with pytest.raises(ValueError, match="area_km2"):
        workload_for(bad_area)  # type: ignore[arg-type]


def _synthetic_counter_report(area: int) -> dict[str, dict[str, object]]:
    workload = workload_for(area)
    b = workload.budget
    return {
        "roads": {
            "noding_candidate_pair_count": b.max_road_candidate_pairs,
            "dijkstra_edge_evaluations": b.max_road_edge_evaluations // 2,
            "astar_edge_evaluations": b.max_road_edge_evaluations // 2,
        },
        "blocks_parcels": {
            "zone_spatial_candidate_pair_count": b.max_block_zone_candidates,
            "parcel_road_candidate_pair_count": b.max_block_road_candidates,
            "parcel_count": 4 * workload.block_grid_size**2,
        },
        "buildings": {
            "proposal_count": workload.building_count,
            "accepted_count": workload.building_count,
        },
        "infrastructure": {
            "subject_count": b.max_infrastructure_subjects,
            "routing_call_count": b.max_routing_calls,
            "routing_call_count_after_greedy": b.max_routing_calls,
        },
        "raster": {
            "sample_count": b.max_raster_cells,
            "windows_written": b.max_raster_windows,
        },
    }


def test_exact_expected_work_passes_and_cache_must_not_reroute() -> None:
    workload = workload_for(25)
    sections = _synthetic_counter_report(25)
    counts = _assert_work_budgets(workload, sections)
    assert counts["infrastructure_routing_calls"] == workload.budget.max_routing_calls
    assert counts["raster_cells"] == workload.budget.max_raster_cells

    sections["infrastructure"]["routing_call_count_after_greedy"] = (
        workload.budget.max_routing_calls + 1
    )
    with pytest.raises(AssertionError, match="coverage cache"):
        _assert_work_budgets(workload, sections)


def test_overbudget_candidate_work_is_a_failing_gate() -> None:
    workload = workload_for(100)
    sections = _synthetic_counter_report(100)
    sections["roads"]["noding_candidate_pair_count"] = (
        workload.budget.max_road_candidate_pairs + 1
    )
    with pytest.raises(AssertionError, match="road_candidate_pairs"):
        _assert_work_budgets(workload, sections)


def test_shortened_territory_raster_is_a_failing_gate() -> None:
    workload = workload_for(25)
    sections = _synthetic_counter_report(25)
    sections["raster"]["sample_count"] = workload.budget.max_raster_cells - 1
    with pytest.raises(AssertionError, match="full territory"):
        _assert_work_budgets(workload, sections)


def test_machine_metadata_is_standard_and_non_identifying() -> None:
    assert PROFILE_SCHEMA == "s14-territory-composite-v1"
    machine = _machine()
    for field in (
        "python", "platform", "architecture", "cpu_count",
        "networkx", "shapely", "rasterio", "git_sha", "ci",
    ):
        assert field in machine
    assert machine["cpu_count"] is None or int(machine["cpu_count"]) > 0
