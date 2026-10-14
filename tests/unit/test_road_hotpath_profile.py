"""S14-T05 regression checks for reproducible hot-path work counters."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from benchmarks.road_hotpath_profile import (
    COUNT_FIELDS,
    PROFILE_SCHEMA,
    TIMING_FIELDS,
    RoadHotpathProfileConfig,
    profile_road_hotpaths,
)
from benchmarks.road_network_reference import (
    RoadNetworkBenchmarkConfig,
    run_reference_road_network_benchmark,
)


def test_exact_candidate_and_routing_counters_on_small_grid() -> None:
    config = RoadNetworkBenchmarkConfig(
        grid_size=6, spacing_m=80.0, snap_query_count=8, path_query_count=4,
    )
    first = run_reference_road_network_benchmark(config)
    second = run_reference_road_network_benchmark(config)
    for field in COUNT_FIELDS:
        assert getattr(first, field) == getattr(second, field), field
    assert first.noding_road_part_count == 12
    assert first.noding_candidate_pair_count == 36
    assert first.noding_junction_count == 36
    assert first.noding_output_part_count == 60
    assert first.noding_suppressed_intersection_count == 0
    assert first.noding_overlap_pair_count == 0
    assert first.snap_candidate_count == 8
    assert first.snap_max_candidates_per_query == 1
    assert first.dijkstra_edge_evaluations > 0
    assert first.astar_edge_evaluations > 0
    assert first.dijkstra_heuristic_evaluations >= first.dijkstra_edge_evaluations // 2
    assert first.astar_heuristic_evaluations >= first.astar_edge_evaluations // 2
    assert first.dijkstra_distance_checksum_m == first.astar_distance_checksum_m


def test_repeated_multiscale_profile_emits_monotonic_work_counts_without_time_slos() -> None:
    report = profile_road_hotpaths(RoadHotpathProfileConfig(
        grid_sizes=(4, 6), repetitions=2, snap_query_count=9,
        path_query_count=4,
    ))
    assert report["schema"] == PROFILE_SCHEMA
    assert report["config"]["grid_sizes"] == (4, 6)
    assert len(report["profiles"]) == 2
    for size, profile in zip((4, 6), report["profiles"], strict=True):
        assert profile["grid_size"] == size
        assert profile["node_count"] == size * size
        assert profile["edge_count"] == 2 * size * (size - 1)
        assert profile["counts"]["noding_candidate_pair_count"] == size * size
        assert profile["counts"]["snap_candidate_count"] == 9
        assert len(profile["runs"]) == 2
        for field in TIMING_FIELDS:
            timing = profile["timings"][field]
            assert 0 <= timing["min_ms"] <= timing["median_ms"] <= timing["max_ms"]
    assert report["profiles"][0]["node_count"] < report["profiles"][1]["node_count"]


@pytest.mark.parametrize(
    "invalid",
    [
        RoadHotpathProfileConfig(grid_sizes=(2,)),
        RoadHotpathProfileConfig(grid_sizes=(6, 8)),
    ],
)
def test_profile_config_valid_examples(invalid: RoadHotpathProfileConfig) -> None:
    assert len(invalid.grid_sizes) > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"grid_sizes": ()},
        {"grid_sizes": (8, 8)},
        {"grid_sizes": (9, 8)},
        {"grid_sizes": (1, 8)},
        {"grid_sizes": (65,)},
        {"grid_sizes": (2, 4, 8, 16, 32)},
        {"repetitions": 0},
        {"repetitions": 6},
        {"snap_query_count": 0},
        {"path_query_count": 513},
    ],
)
def test_profile_rejects_unbounded_or_ambiguous_workloads(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        RoadHotpathProfileConfig(**kwargs)


def test_cli_writes_full_reproducible_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "network-profile.json"
    completed = subprocess.run(
        [
            sys.executable, "-m", "benchmarks.road_hotpath_profile",
            "--grid-sizes", "4", "6", "--repetitions", "1",
            "--snap-queries", "8", "--path-queries", "4",
            "--output", str(output),
        ],
        capture_output=True, text=True, check=True,
    )
    assert "noding_pairs=36" in completed.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema"] == PROFILE_SCHEMA
    assert len(report["profiles"]) == 2
    assert report["profiles"][1]["counts"]["noding_candidate_pair_count"] == 36
