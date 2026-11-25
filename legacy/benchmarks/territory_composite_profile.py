"""S14-T07 reproducible 25/100 km² composite domain workload evidence.

Runs five *real* canonical reference implementations with one shared
metric territory scale, deterministic workload sizes, operation budgets and
machine context. Component fixtures are synthetic and independent: this is
NOT a production Stage DAG, geographic end-to-end scenario or latency SLO.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import resource
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import networkx
import rasterio
import shapely

from benchmarks.block_parcel_reference import (
    BlockParcelBenchmarkConfig,
    run_reference_block_parcel_benchmark,
)
from benchmarks.building_reference import (
    BuildingBenchmarkConfig,
    run_reference_building_benchmark,
)
from benchmarks.infrastructure_reference import (
    InfrastructureBenchmarkConfig,
    run_reference_infrastructure_benchmark,
)
from benchmarks.raster_memory_reference import run_raster_memory_profile
from benchmarks.road_network_reference import (
    RoadNetworkBenchmarkConfig,
    run_reference_road_network_benchmark,
)

PROFILE_SCHEMA = "s14-territory-composite-v1"
ALLOWED_AREAS_KM2 = frozenset((25, 100))
WORKING_SRID = 3857
RASTER_RESOLUTION_M = 10
ROAD_GRID_SPACING_M = 250
RASTER_TILE_SIZE = 256


@dataclass(frozen=True, slots=True)
class ScaleBudget:
    """Hard operation limits, and explicitly *advisory* hardware thresholds."""

    max_raster_cells: int
    max_raster_windows: int
    max_road_candidate_pairs: int
    max_road_edge_evaluations: int
    max_block_zone_candidates: int
    max_block_road_candidates: int
    max_building_proposals: int
    max_infrastructure_subjects: int
    max_routing_calls: int
    advisory_total_wall_ms: float
    advisory_process_peak_rss_mib: float


@dataclass(frozen=True, slots=True)
class TerritoryWorkload:
    """Area drives every fixture's dimensional/operation configuration."""

    area_km2: int
    side_m: int
    road_grid_size: int
    block_grid_size: int
    building_count: int
    infrastructure_demands: int
    infrastructure_candidates: int
    infrastructure_facilities: int
    raster_dimension: int
    budget: ScaleBudget

    def __post_init__(self) -> None:
        if self.area_km2 not in ALLOWED_AREAS_KM2:
            raise ValueError("area must be exactly 25 or 100 km2")
        if self.side_m**2 != self.area_km2 * 1_000_000:
            raise ValueError("metric territory side does not match declared area")
        if self.raster_dimension * RASTER_RESOLUTION_M != self.side_m:
            raise ValueError("raster extent must match declared territory side")
        if (self.road_grid_size - 1) * ROAD_GRID_SPACING_M != self.side_m:
            raise ValueError("road reference extent must match declared territory side")


def workload_for(area_km2: int) -> TerritoryWorkload:
    if type(area_km2) is not int or area_km2 not in ALLOWED_AREAS_KM2:
        raise ValueError("area_km2 must be exactly 25 or 100")
    side_m = math.isqrt(area_km2 * 1_000_000)
    if area_km2 == 25:
        block_size, buildings, demands, candidates, facilities = 12, 1000, 64, 12, 8
    else:
        block_size, buildings, demands, candidates, facilities = 24, 3000, 128, 24, 12
    grid = side_m // ROAD_GRID_SPACING_M + 1
    raster_dimension = side_m // RASTER_RESOLUTION_M
    raster_windows = math.ceil(raster_dimension / RASTER_TILE_SIZE) ** 2
    path_queries = 16
    max_routing_edges = 2 * (2 * grid * (grid - 1)) * path_queries
    routing_calls = candidates * math.ceil(demands / 16)
    return TerritoryWorkload(
        area_km2=area_km2,
        side_m=side_m,
        road_grid_size=grid,
        block_grid_size=block_size,
        building_count=buildings,
        infrastructure_demands=demands,
        infrastructure_candidates=candidates,
        infrastructure_facilities=facilities,
        raster_dimension=raster_dimension,
        budget=ScaleBudget(
            max_raster_cells=raster_dimension**2,
            max_raster_windows=raster_windows,
            max_road_candidate_pairs=grid**2,
            max_road_edge_evaluations=max_routing_edges,
            max_block_zone_candidates=block_size**2,
            max_block_road_candidates=block_size**2,
            max_building_proposals=buildings,
            max_infrastructure_subjects=demands * candidates,
            max_routing_calls=routing_calls,
            advisory_total_wall_ms=240_000.0,
            advisory_process_peak_rss_mib=1_536.0,
        ),
    )


def _rss_peak_mib() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(raw / (1024.0 if sys.platform.startswith("linux") else 1024.0**2), 3)


def _machine() -> dict[str, object]:
    """Enough machine context to compare runs, without host identifiers."""

    cpu_model: str | None = None
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("model name"):
                    cpu_model = line.partition(":")[2].strip()
                    break
        except OSError:
            pass
    total_memory_mib: int | None = None
    if hasattr(os, "sysconf"):
        try:
            total_memory_mib = round(
                int(os.sysconf("SC_PAGE_SIZE"))
                * int(os.sysconf("SC_PHYS_PAGES"))
                / 1024**2
            )
        except (ValueError, OSError):
            pass
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "cpu_model": cpu_model,
        "cpu_count": os.cpu_count(),
        "total_physical_memory_mib": total_memory_mib,
        "networkx": networkx.__version__,
        "shapely": shapely.__version__,
        "rasterio": rasterio.__version__,
        "git_sha": os.getenv("GITHUB_SHA"),
        "ci": os.getenv("CI") == "true",
    }


def _require_within(label: str, observed: int, limit: int) -> None:
    if observed > limit:
        raise AssertionError(f"{label} exceeds reference work budget: {observed} > {limit}")
    if observed < 0:
        raise AssertionError(f"{label} returned negative work count")


def _assert_work_budgets(
    workload: TerritoryWorkload, sections: dict[str, dict[str, Any]]
) -> dict[str, int]:
    b = workload.budget
    roads = sections["roads"]
    blocks = sections["blocks_parcels"]
    buildings = sections["buildings"]
    infrastructure = sections["infrastructure"]
    raster = sections["raster"]

    observed = {
        "raster_cells": int(raster["sample_count"]),
        "raster_windows": int(raster["windows_written"]),
        "road_candidate_pairs": int(roads["noding_candidate_pair_count"]),
        "road_dijkstra_edge_evaluations": int(roads["dijkstra_edge_evaluations"]),
        "road_astar_edge_evaluations": int(roads["astar_edge_evaluations"]),
        "block_zone_candidates": int(blocks["zone_spatial_candidate_pair_count"]),
        "block_road_candidates": int(blocks["parcel_road_candidate_pair_count"]),
        "building_proposals": int(buildings["proposal_count"]),
        "infrastructure_subjects": int(infrastructure["subject_count"]),
        "infrastructure_routing_calls": int(infrastructure["routing_call_count"]),
    }
    limits = {
        "raster_cells": b.max_raster_cells,
        "raster_windows": b.max_raster_windows,
        "road_candidate_pairs": b.max_road_candidate_pairs,
        "road_dijkstra_edge_evaluations": b.max_road_edge_evaluations,
        "road_astar_edge_evaluations": b.max_road_edge_evaluations,
        "block_zone_candidates": b.max_block_zone_candidates,
        "block_road_candidates": b.max_block_road_candidates,
        "building_proposals": b.max_building_proposals,
        "infrastructure_subjects": b.max_infrastructure_subjects,
        "infrastructure_routing_calls": b.max_routing_calls,
    }
    for name, count in observed.items():
        _require_within(name, count, limits[name])

    # Fixture identity and full coverage are correctness gates, not perf scores.
    if observed["raster_cells"] != b.max_raster_cells:
        raise AssertionError("raster reference lost full territory coverage")
    if observed["raster_windows"] != b.max_raster_windows:
        raise AssertionError("raster reference changed its tiled output")
    if observed["road_candidate_pairs"] != b.max_road_candidate_pairs:
        raise AssertionError("road reference candidate count changed")
    if observed["building_proposals"] != workload.building_count:
        raise AssertionError("building fixture changed proposal count")
    if int(buildings["accepted_count"]) != workload.building_count:
        raise AssertionError("building placement lost accepted proposals")
    if observed["infrastructure_subjects"] != b.max_infrastructure_subjects:
        raise AssertionError("infrastructure reference lost candidate/demand subjects")
    if observed["infrastructure_routing_calls"] != b.max_routing_calls:
        raise AssertionError("infrastructure routing cache contract changed")
    if int(infrastructure["routing_call_count_after_greedy"]) != b.max_routing_calls:
        raise AssertionError("greedy placement repeated routing despite coverage cache")
    if blocks["parcel_count"] != 4 * workload.block_grid_size**2:
        raise AssertionError("block reference changed its parcel count")
    return observed


def run_territory_profile(area_km2: int) -> dict[str, object]:
    """Measure a composite reference profile in a fresh caller process."""

    workload = workload_for(area_km2)
    b = workload.budget
    started = perf_counter()
    phases_ms: dict[str, float] = {}
    sections: dict[str, dict[str, Any]] = {}

    def measure(name: str, execute: Any) -> None:
        phase_start = perf_counter()
        result = execute()
        phases_ms[name] = round((perf_counter() - phase_start) * 1000.0, 3)
        sections[name] = asdict(result) if hasattr(result, "__dataclass_fields__") else result

    road_config = RoadNetworkBenchmarkConfig(
        grid_size=workload.road_grid_size,
        spacing_m=ROAD_GRID_SPACING_M,
        snap_query_count=64,
        path_query_count=16,
    )
    measure("roads", lambda: run_reference_road_network_benchmark(road_config))

    block_config = BlockParcelBenchmarkConfig(
        grid_size=workload.block_grid_size,
        column_spacing_m=workload.side_m / workload.block_grid_size,
        row_spacing_m=workload.side_m / workload.block_grid_size,
    )
    measure("blocks_parcels", lambda: run_reference_block_parcel_benchmark(block_config))

    building_grid_side = math.ceil(math.sqrt(workload.building_count))
    building_cell_size = workload.side_m / building_grid_side
    building_config = BuildingBenchmarkConfig(
        building_count=workload.building_count,
        cell_size_m=building_cell_size,
        footprint_size_m=building_cell_size / 2.0,
    )
    measure("buildings", lambda: run_reference_building_benchmark(building_config))

    infrastructure_config = InfrastructureBenchmarkConfig(
        demand_count=workload.infrastructure_demands,
        candidate_count=workload.infrastructure_candidates,
        demand_batch_size=16,
        max_facilities=workload.infrastructure_facilities,
        spacing_m=workload.side_m / (
            workload.infrastructure_demands + workload.infrastructure_candidates - 1
        ),
    )
    measure(
        "infrastructure",
        lambda: run_reference_infrastructure_benchmark(infrastructure_config),
    )

    measure(
        "raster",
        lambda: run_raster_memory_profile(
            width=workload.raster_dimension,
            height=workload.raster_dimension,
            tile_size=RASTER_TILE_SIZE,
            gdal_cache_mb=64,
        ),
    )

    counts = _assert_work_budgets(workload, sections)
    total_ms = round((perf_counter() - started) * 1000.0, 3)
    peak_rss = _rss_peak_mib()
    return {
        "profile_schema": PROFILE_SCHEMA,
        "fixture": "synthetic-composite-not-stage-dag",
        "area_km2": workload.area_km2,
        "side_m": workload.side_m,
        "working_srid": WORKING_SRID,
        "raster_resolution_m": RASTER_RESOLUTION_M,
        "machine": _machine(),
        "workload": asdict(workload),
        "budgets": {
            "hard_work_limits": {
                key: value for key, value in asdict(b).items()
                if key.startswith("max_")
            },
            "observed": counts,
            "hard_pass": True,
            "advisory_total_wall_ms": b.advisory_total_wall_ms,
            "advisory_process_peak_rss_mib": b.advisory_process_peak_rss_mib,
            "advisory_wall_within": total_ms <= b.advisory_total_wall_ms,
            "advisory_peak_rss_within": peak_rss <= b.advisory_process_peak_rss_mib,
        },
        "phase_wall_ms": phases_ms,
        "total_wall_ms": total_ms,
        "process_peak_rss_mib": peak_rss,
        "rss_scope": "whole process high-water; not phase memory or per-worker SLO",
        "sections": sections,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area-km2", type=int, required=True, choices=(25, 100))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_territory_profile(args.area_km2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"S14 area={report['area_km2']}km² total={report['total_wall_ms']}ms "
        f"peak_rss={report['process_peak_rss_mib']}MiB "
        f"hard_work_pass={report['budgets']['hard_pass']}"
    )
    for name, elapsed in report["phase_wall_ms"].items():
        print(f"  {name}: {elapsed}ms")
    print(f"Machine-readable evidence: {args.output}")


if __name__ == "__main__":
    main()
