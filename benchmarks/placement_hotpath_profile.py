"""Multi-scale measured S14 building and infrastructure hotspot profiles.

Runs the production domain reference work and collects cProfile call counts/
cumulative CPU, not guessed bottlenecks. Timing evidence is advisory, not SLO.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import math
import platform
import pstats
import resource
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from benchmarks.building_reference import (
    BuildingBenchmarkConfig,
    run_reference_building_benchmark,
)
from benchmarks.infrastructure_reference import (
    InfrastructureBenchmarkConfig,
    run_reference_infrastructure_benchmark,
)

PROFILE_SCHEMA = "s14-placement-hotspots-v1"
MAX_REPETITIONS = 4
MAX_BUILDINGS = 10_000
MAX_DEMANDS = 256
MAX_CANDIDATES = 64


@dataclass(frozen=True, slots=True)
class HotspotFunction:
    path: str
    function: str
    line: int
    calls: int
    cumulative_ms: float
    self_ms: float


def _profile_callable(
    execute: Callable[[], Any],
    *,
    repetitions: int,
) -> dict[str, object]:
    if isinstance(repetitions, bool) or not isinstance(repetitions, int):
        raise ValueError("repetitions must be an integer")
    if not 1 <= repetitions <= MAX_REPETITIONS:
        raise ValueError(f"repetitions must be 1..{MAX_REPETITIONS}")

    durations_ms: list[float] = []
    digests: list[str] = []
    primary: Any = None
    profiler = cProfile.Profile()
    for index in range(repetitions):
        started = perf_counter()
        if index == 0:
            profiler.enable()
        outcome = execute()
        if index == 0:
            profiler.disable()
            primary = outcome
        durations_ms.append((perf_counter() - started) * 1000.0)
        digests.append(outcome.deterministic_digest)

    if len(set(digests)) != 1:
        raise RuntimeError("reference workload changed deterministic digest between repetitions")

    # Cumulative times of nested functions overlap; never add them as a phase
    # total. cProfile results are recorded for just the first iteration.
    stats = pstats.Stats(profiler)
    hotspot_rows: list[HotspotFunction] = []
    for key, values in stats.stats.items():
        path, line, func = key
        if not (
            "core/urban_generator/buildings/" in path
            or "core/urban_generator/infrastructure/" in path
            or "core/urban_generator/roads/" in path
        ):
            continue
        _primitive_calls, calls, self_seconds, cumulative_seconds, _callers = values
        hotspot_rows.append(
            HotspotFunction(
                path=path.split("core/urban_generator/", 1)[-1],
                function=func,
                line=line,
                calls=calls,
                cumulative_ms=round(cumulative_seconds * 1000.0, 3),
                self_ms=round(self_seconds * 1000.0, 3),
            )
        )
    hotspot_rows.sort(key=lambda item: (-item.cumulative_ms, item.path, item.line))
    return {
        "result": asdict(primary),
        "repetitions": repetitions,
        "first_profiled_wall_ms": round(durations_ms[0], 3),
        "unprofiled_wall_ms": [round(value, 3) for value in durations_ms[1:]],
        "unprofiled_median_ms": (
            round(statistics.median(durations_ms[1:]), 3)
            if repetitions > 1 else None
        ),
        "cprofile_top_functions": [asdict(row) for row in hotspot_rows[:12]],
    }


def _infrastructure_config(demand_count: int, candidate_count: int) -> InfrastructureBenchmarkConfig:
    if not 1 <= demand_count <= MAX_DEMANDS:
        raise ValueError("demand_count must be 1..256")
    if not 1 <= candidate_count <= MAX_CANDIDATES:
        raise ValueError("candidate_count must be 1..64")
    # Preserve positive unmet demand across every accepted placement.
    max_facilities = min(candidate_count, max(1, demand_count // 8))
    if max_facilities * 4.0 > demand_count:
        raise ValueError("infrastructure fixture cannot retain positive demand")
    return InfrastructureBenchmarkConfig(
        demand_count=demand_count,
        candidate_count=candidate_count,
        demand_batch_size=min(16, demand_count),
        max_facilities=max_facilities,
        capacity=4.0,
    )


def _scale(text: str) -> tuple[int, int]:
    left, sep, right = text.lower().partition("x")
    if not sep:
        raise ValueError("infrastructure scale must use DEMANDSxCANDIDATES")
    try:
        demands, candidates = int(left), int(right)
    except ValueError as exc:
        raise ValueError("infrastructure scale must contain integer counts") from exc
    _infrastructure_config(demands, candidates)
    return demands, candidates


def run_placement_hotspot_profile(
    *,
    building_counts: tuple[int, ...] = (1000, 3000),
    infrastructure_scales: tuple[tuple[int, int], ...] = ((32, 8), (64, 16)),
    repetitions: int = 2,
) -> dict[str, object]:
    if not building_counts or not infrastructure_scales:
        raise ValueError("at least one building and infrastructure size is required")
    if len(building_counts) > 4 or len(infrastructure_scales) > 4:
        raise ValueError("at most four sizes per workload")
    if any(type(count) is not int or not 1 <= count <= MAX_BUILDINGS for count in building_counts):
        raise ValueError("building count must be 1..10000")

    building_profiles = []
    for count in building_counts:
        cfg = BuildingBenchmarkConfig(building_count=count)
        building_profiles.append(
            {"size": count, **_profile_callable(
                lambda cfg=cfg: run_reference_building_benchmark(cfg),
                repetitions=repetitions,
            )}
        )

    infrastructure_profiles = []
    for demands, candidates in infrastructure_scales:
        cfg = _infrastructure_config(demands, candidates)
        infrastructure_profiles.append(
            {"size": {"demands": demands, "candidates": candidates},
             **_profile_callable(
                 lambda cfg=cfg: run_reference_infrastructure_benchmark(cfg),
                 repetitions=repetitions,
             )}
        )

    rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_peak_mib = (
        rss_raw / 1024.0 if platform.system() == "Linux"
        else rss_raw / (1024.0 * 1024.0)
    )
    return {
        "schema": PROFILE_SCHEMA,
        "fixture": "deterministic-synthetic-reference",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "rss_peak_process_mib": round(rss_peak_mib, 3),
        "rss_peak_scope": "entire process, all fixtures, high-water not per phase",
        "timing_note": "first repetition includes cProfile overhead; not a performance SLO",
        "buildings": building_profiles,
        "infrastructure": infrastructure_profiles,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building-counts", nargs="+", type=int, default=[1000, 3000])
    parser.add_argument("--infrastructure-scales", nargs="+", default=["32x8", "64x16"])
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scales = tuple(_scale(raw) for raw in args.infrastructure_scales)
    report = run_placement_hotspot_profile(
        building_counts=tuple(args.building_counts),
        infrastructure_scales=scales,
        repetitions=args.repetitions,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for group in ("buildings", "infrastructure"):
        for item in report[group]:
            top = item["cprofile_top_functions"][:3]
            print(f"{group} size={item['size']} median-unprofiled-ms="
                  f"{item['unprofiled_median_ms']} "
                  f"hotspots={[(fn['function'], fn['cumulative_ms']) for fn in top]}")
    print(f"Machine-readable hotspot evidence: {args.output}")


if __name__ == "__main__":
    main()
