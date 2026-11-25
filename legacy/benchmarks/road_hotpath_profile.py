"""S14-T05 noding/snapping/routing timings and actual candidate counters.

Profiles the existing deterministic reference graph at increasing sizes with
repeated, independently timed runs. Timings are observations, never a CI SLO.
"""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

import networkx as nx
import shapely

from benchmarks.road_network_reference import (
    REFERENCE_FIXTURE_NAME,
    RoadNetworkBenchmarkConfig,
    RoadNetworkBenchmarkResult,
    run_reference_road_network_benchmark,
)

PROFILE_SCHEMA = "s14-road-hotpaths-v1"
TIMING_FIELDS = (
    "snapping_index_build_ms",
    "snapping_query_ms",
    "semantic_noding_ms",
    "pathfinding_dijkstra_ms",
    "pathfinding_astar_ms",
)
COUNT_FIELDS = (
    "node_count",
    "edge_count",
    "semantic_road_count",
    "snap_match_count",
    "snap_candidate_count",
    "snap_max_candidates_per_query",
    "noding_candidate_pair_count",
    "noding_road_part_count",
    "noding_suppressed_intersection_count",
    "noding_overlap_pair_count",
    "noding_junction_count",
    "noding_output_part_count",
    "dijkstra_edge_evaluations",
    "astar_edge_evaluations",
    "dijkstra_heuristic_evaluations",
    "astar_heuristic_evaluations",
)


@dataclass(frozen=True, slots=True)
class RoadHotpathProfileConfig:
    grid_sizes: tuple[int, ...] = (8, 16, 32)
    repetitions: int = 2
    spacing_m: float = 100.0
    snap_query_count: int = 128
    path_query_count: int = 32

    def __post_init__(self) -> None:
        if (
            not isinstance(self.grid_sizes, tuple)
            or not 1 <= len(self.grid_sizes) <= 4
            or any(type(n) is not int or not 2 <= n <= 64 for n in self.grid_sizes)
            or tuple(sorted(set(self.grid_sizes))) != self.grid_sizes
        ):
            raise ValueError("grid_sizes must be 1..4 distinct increasing sizes 2..64")
        if type(self.repetitions) is not int or not 1 <= self.repetitions <= 5:
            raise ValueError("repetitions must be between 1 and 5")
        if type(self.snap_query_count) is not int or not 1 <= self.snap_query_count <= 4096:
            raise ValueError("snap_query_count must be 1..4096")
        if type(self.path_query_count) is not int or not 1 <= self.path_query_count <= 512:
            raise ValueError("path_query_count must be 1..512")
        # The existing canonical reference config validates metric spacing.
        RoadNetworkBenchmarkConfig(grid_size=2, spacing_m=self.spacing_m)


DEFAULT_PROFILE_CONFIG = RoadHotpathProfileConfig()


def _timings(repeats: list[RoadNetworkBenchmarkResult]) -> dict[str, dict[str, float]]:
    return {
        field: {
            "min_ms": round(min(float(getattr(r, field)) for r in repeats), 3),
            "median_ms": round(median(float(getattr(r, field)) for r in repeats), 3),
            "max_ms": round(max(float(getattr(r, field)) for r in repeats), 3),
        }
        for field in TIMING_FIELDS
    }


def _one_scale(
    *,
    size: int,
    config: RoadHotpathProfileConfig,
) -> dict[str, object]:
    benchmark = RoadNetworkBenchmarkConfig(
        grid_size=size,
        spacing_m=config.spacing_m,
        snap_query_count=config.snap_query_count,
        path_query_count=config.path_query_count,
    )
    repeats = [
        run_reference_road_network_benchmark(benchmark)
        for _ in range(config.repetitions)
    ]
    reference = repeats[0]
    for result in repeats:
        for key in COUNT_FIELDS:
            if getattr(result, key) != getattr(reference, key):
                raise AssertionError(f"non-deterministic reference counter: {key}")
        if (
            result.dijkstra_distance_checksum_m != reference.dijkstra_distance_checksum_m
            or result.astar_distance_checksum_m != reference.astar_distance_checksum_m
        ):
            raise AssertionError("routing checksums changed between equivalent runs")
        if result.noding_candidate_pair_count != size * size:
            raise AssertionError("square grid lost its bounded noding candidate contract")
        if result.snap_candidate_count != config.snap_query_count:
            raise AssertionError("reference snapping no longer has one exact candidate/query")
        if (
            result.noding_overlap_pair_count
            or result.noding_suppressed_intersection_count
        ):
            raise AssertionError("reference grid unexpectedly contains overlap/grade exclusions")
        if not 0 < result.dijkstra_edge_evaluations <= (
            2 * result.edge_count * config.path_query_count
        ):
            raise AssertionError("Dijkstra relaxed more edges than the bounded graph")
        if not 0 < result.astar_edge_evaluations <= (
            2 * result.edge_count * config.path_query_count
        ):
            raise AssertionError("A* relaxed more edges than the bounded graph")

    return {
        "grid_size": size,
        "node_count": reference.node_count,
        "edge_count": reference.edge_count,
        "counts": {key: getattr(reference, key) for key in COUNT_FIELDS},
        "dijkstra_distance_checksum_m": reference.dijkstra_distance_checksum_m,
        "astar_distance_checksum_m": reference.astar_distance_checksum_m,
        "timings": _timings(repeats),
        "runs": [asdict(r) for r in repeats],
    }


def profile_road_hotpaths(
    config: RoadHotpathProfileConfig = DEFAULT_PROFILE_CONFIG,
) -> dict[str, object]:
    if not isinstance(config, RoadHotpathProfileConfig):
        raise TypeError("config must be RoadHotpathProfileConfig")
    return {
        "schema": PROFILE_SCHEMA,
        "fixture": REFERENCE_FIXTURE_NAME,
        "python": platform.python_version(),
        "networkx": nx.__version__,
        "shapely": shapely.__version__,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "config": asdict(config),
        "profiles": [
            _one_scale(size=size, config=config) for size in config.grid_sizes
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-sizes", type=int, nargs="+", default=[8, 16, 32])
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--snap-queries", type=int, default=128)
    parser.add_argument("--path-queries", type=int, default=32)
    parser.add_argument("--spacing-m", type=float, default=100.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = profile_road_hotpaths(
        RoadHotpathProfileConfig(
            grid_sizes=tuple(args.grid_sizes),
            repetitions=args.repetitions,
            snap_query_count=args.snap_queries,
            path_query_count=args.path_queries,
            spacing_m=args.spacing_m,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"S14 road hotpath profile: {report['fixture']} ({report['python']})")
    for scale in report["profiles"]:
        timing = scale["timings"]
        counts = scale["counts"]
        print(
            f"  grid {scale['grid_size']} nodes={scale['node_count']} "
            f"noding={timing['semantic_noding_ms']['median_ms']}ms "
            f"snap={timing['snapping_query_ms']['median_ms']}ms "
            f"dijkstra={timing['pathfinding_dijkstra_ms']['median_ms']}ms "
            f"astar={timing['pathfinding_astar_ms']['median_ms']}ms "
            f"noding_pairs={counts['noding_candidate_pair_count']} "
            f"snap_candidates={counts['snap_candidate_count']} "
            f"route_edges={counts['dijkstra_edge_evaluations']}/"
            f"{counts['astar_edge_evaluations']}"
        )
    print(f"Full measured JSON: {args.output}")


if __name__ == "__main__":
    main()
