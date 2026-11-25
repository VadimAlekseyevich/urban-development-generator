import pytest

from benchmarks.road_network_reference import (
    REFERENCE_FIXTURE_NAME,
    RoadNetworkBenchmarkConfig,
    build_reference_fixture,
    run_reference_road_network_benchmark,
)


def test_reference_road_fixture_has_expected_grid_topology() -> None:
    config = RoadNetworkBenchmarkConfig(
        grid_size=6,
        spacing_m=80.0,
        snap_query_count=8,
        path_query_count=4,
    )

    fixture = build_reference_fixture(config)

    assert fixture.graph.number_of_nodes() == 36
    assert fixture.graph.number_of_edges() == 60
    assert len(fixture.semantic_roads) == 12
    assert len(fixture.snap_targets) == 36
    assert len(fixture.snap_queries) == 8
    assert len(fixture.path_pairs) == 4


def test_reference_road_benchmark_executes_all_hot_paths() -> None:
    result = run_reference_road_network_benchmark(
        RoadNetworkBenchmarkConfig(
            grid_size=6,
            spacing_m=80.0,
            snap_query_count=8,
            path_query_count=4,
        )
    )

    assert result.fixture_name == REFERENCE_FIXTURE_NAME
    assert result.node_count == 36
    assert result.edge_count == 60
    assert result.semantic_road_count == 12
    assert result.snap_match_count == 8
    assert result.noding_junction_count == 36
    assert result.noding_output_part_count == 60
    assert result.noding_candidate_pair_count >= 36
    assert result.dijkstra_distance_checksum_m > 0.0
    assert result.astar_distance_checksum_m == pytest.approx(
        result.dijkstra_distance_checksum_m
    )
    assert result.snapping_index_build_ms >= 0.0
    assert result.snapping_query_ms >= 0.0
    assert result.semantic_noding_ms >= 0.0
    assert result.pathfinding_dijkstra_ms >= 0.0
    assert result.pathfinding_astar_ms >= 0.0


def test_reference_benchmark_config_rejects_unbounded_or_empty_workloads() -> None:
    with pytest.raises(ValueError, match="grid_size"):
        RoadNetworkBenchmarkConfig(grid_size=1)
    with pytest.raises(ValueError, match="snap_query_count"):
        RoadNetworkBenchmarkConfig(snap_query_count=0)
    with pytest.raises(ValueError, match="path_query_count"):
        RoadNetworkBenchmarkConfig(path_query_count=0)
