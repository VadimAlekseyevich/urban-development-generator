"""Regression gates for measured S14 building/infrastructure hotspots."""

from __future__ import annotations

import pytest

from benchmarks.building_reference import BuildingBenchmarkConfig, run_reference_building_benchmark
from benchmarks.infrastructure_reference import (
    InfrastructureBenchmarkConfig,
    run_reference_infrastructure_benchmark,
)
from benchmarks.placement_hotpath_profile import (
    PROFILE_SCHEMA,
    _infrastructure_config,
    _scale,
    run_placement_hotspot_profile,
)


def test_building_reference_exposes_real_spatial_work() -> None:
    result = run_reference_building_benchmark(
        BuildingBenchmarkConfig(building_count=36)
    )
    assert result.proposal_count == 36
    assert result.placement_iteration_count == 36
    assert result.accepted_count == 36
    assert result.spacing_rejected_count == 0
    assert result.target_window_rejected_count == 0
    assert result.proposal_creation_ms >= 0


def test_infrastructure_greedy_reuses_accessibility_cache_without_rerouting() -> None:
    result = run_reference_infrastructure_benchmark(
        InfrastructureBenchmarkConfig(
            demand_count=12,
            candidate_count=4,
            demand_batch_size=3,
            capacity=2.0,
            max_facilities=3,
        )
    )
    assert result.coverage_cache_entry_count == 4
    assert result.coverage_cache_row_count == 12 * 4
    assert result.benefit_evaluation_count == 4 + 3 + 2
    assert result.routing_call_count == 16
    assert result.routing_call_count_after_greedy == result.routing_call_count
    assert result.benefit_calculation_ms >= 0
    assert result.candidate_selection_ms >= 0
    assert result.cache_application_ms >= 0
    assert (
        result.benefit_calculation_ms
        + result.candidate_selection_ms
        + result.cache_application_ms
        <= result.greedy_ms + 0.01
    )


def test_hotspot_profile_matches_real_reference_work_and_digest_is_stable() -> None:
    profile = run_placement_hotspot_profile(
        building_counts=(36,),
        infrastructure_scales=((12, 4),),
        repetitions=2,
    )
    assert profile["schema"] == PROFILE_SCHEMA
    assert profile["fixture"] == "deterministic-synthetic-reference"
    assert profile["rss_peak_process_mib"] > 0
    for group in ("buildings", "infrastructure"):
        entry = profile[group][0]
        assert entry["repetitions"] == 2
        assert len(entry["unprofiled_wall_ms"]) == 1
        assert entry["unprofiled_median_ms"] >= 0
        assert len(entry["result"]["deterministic_digest"]) == 64
        functions = entry["cprofile_top_functions"]
        assert functions
        assert all(func["calls"] > 0 for func in functions)
        assert all(func["cumulative_ms"] >= 0 for func in functions)
        assert len(functions) <= 12


def test_hotspot_workload_bounds_are_strict() -> None:
    with pytest.raises(ValueError, match="building count"):
        run_placement_hotspot_profile(building_counts=(10_001,))
    with pytest.raises(ValueError, match="sizes"):
        run_placement_hotspot_profile(infrastructure_scales=())
    with pytest.raises(ValueError, match="repetitions"):
        run_placement_hotspot_profile(building_counts=(10,), repetitions=5)
    with pytest.raises(ValueError, match="infrastructure scale"):
        _scale("invalid")
    with pytest.raises(ValueError, match="demand_count"):
        _infrastructure_config(257, 8)
    with pytest.raises(ValueError, match="candidate_count"):
        _infrastructure_config(32, 65)
