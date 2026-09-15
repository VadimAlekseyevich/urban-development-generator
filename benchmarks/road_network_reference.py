from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from math import isclose
from time import perf_counter_ns

import networkx as nx
from shapely.geometry import LineString

from core.urban_generator.domain import (
    NetworkNodeRef,
    NetworkPoint,
    NetworkRoutingAlgorithm,
    WorkingCRS,
)
from core.urban_generator.roads.networkx_backend import NetworkXBackend
from core.urban_generator.roads.semantic_noding import SemanticNoder, SemanticRoad
from core.urban_generator.roads.spatial_snapping import SpatialSnapIndex, SpatialSnapTarget

REFERENCE_FIXTURE_NAME = "synthetic-grid-v1"


@dataclass(frozen=True, slots=True)
class RoadNetworkBenchmarkConfig:
    """Deterministic workload definition used to compare road-network hot paths."""

    grid_size: int = 32
    spacing_m: float = 100.0
    snap_query_count: int = 128
    path_query_count: int = 32
    working_srid: int = 3857

    def __post_init__(self) -> None:
        if isinstance(self.grid_size, bool) or not isinstance(self.grid_size, int):
            raise ValueError("grid_size must be an integer")
        if self.grid_size < 2:
            raise ValueError("grid_size must be at least 2")
        if isinstance(self.spacing_m, bool) or not isinstance(self.spacing_m, (int, float)):
            raise ValueError("spacing_m must be a number")
        if self.spacing_m <= 0.0:
            raise ValueError("spacing_m must be positive")
        for field_name, value in (
            ("snap_query_count", self.snap_query_count),
            ("path_query_count", self.path_query_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        _ = WorkingCRS(self.working_srid)


REFERENCE_CONFIG = RoadNetworkBenchmarkConfig()


@dataclass(frozen=True, slots=True)
class RoadNetworkBenchmarkFixture:
    config: RoadNetworkBenchmarkConfig
    semantic_roads: tuple[SemanticRoad, ...]
    snap_targets: tuple[SpatialSnapTarget, ...]
    snap_queries: tuple[NetworkPoint, ...]
    graph: nx.Graph
    path_pairs: tuple[tuple[NetworkNodeRef, NetworkNodeRef], ...]


@dataclass(frozen=True, slots=True)
class RoadNetworkBenchmarkResult:
    fixture_name: str
    working_srid: int
    grid_size: int
    node_count: int
    edge_count: int
    semantic_road_count: int
    snap_query_count: int
    path_query_count: int
    snapping_index_build_ms: float
    snapping_query_ms: float
    semantic_noding_ms: float
    pathfinding_dijkstra_ms: float
    pathfinding_astar_ms: float
    noding_candidate_pair_count: int
    noding_junction_count: int
    noding_output_part_count: int
    snap_match_count: int
    dijkstra_distance_checksum_m: float
    astar_distance_checksum_m: float


def _node_id(row: int, column: int) -> str:
    return f"n-{row}-{column}"


def build_reference_fixture(
    config: RoadNetworkBenchmarkConfig = REFERENCE_CONFIG,
) -> RoadNetworkBenchmarkFixture:
    """Build one fixed square grid shared by snapping, noding and routing benchmarks."""

    size = config.grid_size
    spacing = float(config.spacing_m)
    graph = nx.Graph()
    snap_targets: list[SpatialSnapTarget] = []

    for row in range(size):
        for column in range(size):
            node_id = _node_id(row, column)
            x_m = column * spacing
            y_m = row * spacing
            graph.add_node(node_id, x_m=x_m, y_m=y_m)
            snap_targets.append(
                SpatialSnapTarget(
                    target_id=node_id,
                    point=NetworkPoint(x_m=x_m, y_m=y_m),
                )
            )

    for row in range(size):
        for column in range(size - 1):
            graph.add_edge(
                _node_id(row, column),
                _node_id(row, column + 1),
                length_m=spacing,
            )
    for column in range(size):
        for row in range(size - 1):
            graph.add_edge(
                _node_id(row, column),
                _node_id(row + 1, column),
                length_m=spacing,
            )

    max_axis = (size - 1) * spacing
    semantic_roads = tuple(
        [
            SemanticRoad(
                road_id=f"row-{row}",
                geometry=LineString(((0.0, row * spacing), (max_axis, row * spacing))),
            )
            for row in range(size)
        ]
        + [
            SemanticRoad(
                road_id=f"column-{column}",
                geometry=LineString(
                    ((column * spacing, 0.0), (column * spacing, max_axis))
                ),
            )
            for column in range(size)
        ]
    )

    node_count = size * size
    snap_queries = tuple(
        NetworkPoint(
            x_m=((index * 7919) % node_count % size) * spacing + min(1.0, spacing / 10.0),
            y_m=((index * 7919) % node_count // size) * spacing + min(0.5, spacing / 20.0),
        )
        for index in range(config.snap_query_count)
    )

    path_pairs = tuple(
        (
            NetworkNodeRef(node_id=_node_id(index % size, 0)),
            NetworkNodeRef(node_id=_node_id(size - 1 - (index % size), size - 1)),
        )
        for index in range(config.path_query_count)
    )

    return RoadNetworkBenchmarkFixture(
        config=config,
        semantic_roads=semantic_roads,
        snap_targets=tuple(snap_targets),
        snap_queries=snap_queries,
        graph=graph,
        path_pairs=path_pairs,
    )


def _elapsed_ms(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1_000_000.0


def _run_paths(
    backend: NetworkXBackend,
    pairs: tuple[tuple[NetworkNodeRef, NetworkNodeRef], ...],
    *,
    algorithm: NetworkRoutingAlgorithm,
) -> float:
    checksum = 0.0
    for source, target in pairs:
        path = backend.shortest_path(source, target, algorithm=algorithm)
        if path is None:
            raise RuntimeError(
                f"reference fixture unexpectedly has no path {source.node_id}->{target.node_id}"
            )
        checksum += path.distance_m
    return checksum


def run_reference_road_network_benchmark(
    config: RoadNetworkBenchmarkConfig = REFERENCE_CONFIG,
) -> RoadNetworkBenchmarkResult:
    """Execute the deterministic reference workload and return timings in milliseconds."""

    fixture = build_reference_fixture(config)

    started = perf_counter_ns()
    snap_index = SpatialSnapIndex(
        targets=fixture.snap_targets,
        working_srid=config.working_srid,
    )
    snap_index_built = perf_counter_ns()

    snap_match_count = 0
    snap_tolerance_m = max(2.0, min(config.spacing_m / 4.0, 25.0))
    for query in fixture.snap_queries:
        if snap_index.snap(query, tolerance_m=snap_tolerance_m) is not None:
            snap_match_count += 1
    snapping_finished = perf_counter_ns()
    if snap_match_count != len(fixture.snap_queries):
        raise RuntimeError(
            "reference snapping workload must match every query: "
            f"{snap_match_count} != {len(fixture.snap_queries)}"
        )

    noder = SemanticNoder(working_srid=config.working_srid)
    noding_started = perf_counter_ns()
    noding_result = noder.node(fixture.semantic_roads)
    noding_finished = perf_counter_ns()

    expected_nodes = config.grid_size * config.grid_size
    expected_edges = 2 * config.grid_size * (config.grid_size - 1)
    if noding_result.diagnostics.junction_count != expected_nodes:
        raise RuntimeError(
            "reference noding junction count changed: "
            f"{noding_result.diagnostics.junction_count} != {expected_nodes}"
        )
    if noding_result.diagnostics.output_part_count != expected_edges:
        raise RuntimeError(
            "reference noding output part count changed: "
            f"{noding_result.diagnostics.output_part_count} != {expected_edges}"
        )

    backend = NetworkXBackend(
        fixture.graph,
        snapshot_id=REFERENCE_FIXTURE_NAME,
        working_crs=WorkingCRS(config.working_srid),
    )
    dijkstra_started = perf_counter_ns()
    dijkstra_checksum = _run_paths(
        backend,
        fixture.path_pairs,
        algorithm=NetworkRoutingAlgorithm.DIJKSTRA,
    )
    dijkstra_finished = perf_counter_ns()

    astar_started = perf_counter_ns()
    astar_checksum = _run_paths(
        backend,
        fixture.path_pairs,
        algorithm=NetworkRoutingAlgorithm.ASTAR,
    )
    astar_finished = perf_counter_ns()
    if not isclose(dijkstra_checksum, astar_checksum, rel_tol=1e-12, abs_tol=1e-9):
        raise RuntimeError("Dijkstra and A* reference checksums diverged")

    return RoadNetworkBenchmarkResult(
        fixture_name=REFERENCE_FIXTURE_NAME,
        working_srid=config.working_srid,
        grid_size=config.grid_size,
        node_count=fixture.graph.number_of_nodes(),
        edge_count=fixture.graph.number_of_edges(),
        semantic_road_count=len(fixture.semantic_roads),
        snap_query_count=len(fixture.snap_queries),
        path_query_count=len(fixture.path_pairs),
        snapping_index_build_ms=_elapsed_ms(started, snap_index_built),
        snapping_query_ms=_elapsed_ms(snap_index_built, snapping_finished),
        semantic_noding_ms=_elapsed_ms(noding_started, noding_finished),
        pathfinding_dijkstra_ms=_elapsed_ms(dijkstra_started, dijkstra_finished),
        pathfinding_astar_ms=_elapsed_ms(astar_started, astar_finished),
        noding_candidate_pair_count=noding_result.diagnostics.candidate_pair_count,
        noding_junction_count=noding_result.diagnostics.junction_count,
        noding_output_part_count=noding_result.diagnostics.output_part_count,
        snap_match_count=snap_match_count,
        dijkstra_distance_checksum_m=dijkstra_checksum,
        astar_distance_checksum_m=astar_checksum,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic S06 road-network reference benchmark."
    )
    parser.add_argument("--grid-size", type=int, default=32)
    parser.add_argument("--spacing-m", type=float, default=100.0)
    parser.add_argument("--snap-queries", type=int, default=128)
    parser.add_argument("--path-queries", type=int, default=32)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = run_reference_road_network_benchmark(
        RoadNetworkBenchmarkConfig(
            grid_size=args.grid_size,
            spacing_m=args.spacing_m,
            snap_query_count=args.snap_queries,
            path_query_count=args.path_queries,
        )
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
