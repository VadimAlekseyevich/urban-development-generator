from __future__ import annotations

import argparse
import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass
from time import perf_counter_ns

from shapely.geometry import box

from core.urban_generator.buildings import (
    BuildingArchetype,
    BuildingAreaMetricsCalculator,
    BuildingAreaSubject,
    BuildingAttributeAssigner,
    BuildingAttributeConfig,
    BuildingAttributeRule,
    BuildingAttributeSubject,
    BuildingPlacementConverger,
    BuildingPlacementProposal,
    BuildingPlacementTargets,
    BuildingSpacingPolicy,
    BuildingUse,
)
from core.urban_generator.domain import (
    CorrelationMetadata,
    RunContext,
    RunMode,
)
from core.urban_generator.zoning import ZoneClass

REFERENCE_FIXTURE_NAME = "building-grid-v1"
WORKING_SRID = 3857


@dataclass(frozen=True, slots=True)
class BuildingBenchmarkConfig:
    """Deterministic large building placement/attribute/metrics workload."""

    building_count: int = 1000
    footprint_size_m: float = 1.0
    cell_size_m: float = 2.0
    floors: int = 2
    working_srid: int = WORKING_SRID

    def __post_init__(self) -> None:
        if (
            isinstance(self.building_count, bool)
            or not isinstance(self.building_count, int)
            or self.building_count <= 0
        ):
            raise ValueError("building_count must be a positive integer")
        for name, value in (
            ("footprint_size_m", self.footprint_size_m),
            ("cell_size_m", self.cell_size_m),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be positive and finite")
        if self.cell_size_m <= self.footprint_size_m:
            raise ValueError("cell_size_m must exceed footprint_size_m")
        if (
            isinstance(self.floors, bool)
            or not isinstance(self.floors, int)
            or self.floors <= 0
        ):
            raise ValueError("floors must be a positive integer")


@dataclass(frozen=True, slots=True)
class BuildingBenchmarkResult:
    fixture_name: str
    building_count: int
    working_srid: int
    target_coverage_ratio: float
    target_far: float
    accepted_count: int
    placement_ms: float
    attribute_assignment_ms: float
    area_metrics_ms: float
    final_coverage_ratio: float
    final_far: float
    total_footprint_area_m2: float
    total_gfa_m2: float
    deterministic_digest: str


def _elapsed_ms(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1_000_000.0


def _context() -> RunContext:
    return RunContext(
        run_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        mode=RunMode.FROM_SCRATCH,
        seed=20260919,
        working_srid=WORKING_SRID,
        config_refs=(),
        correlation=CorrelationMetadata(
            correlation_id="building-reference-benchmark",
        ),
    )


def _attribute_config(floors: int) -> BuildingAttributeConfig:
    return BuildingAttributeConfig(
        version="building-reference-v1",
        rules=(
            BuildingAttributeRule(
                zone_class=ZoneClass.RESIDENTIAL,
                archetype=BuildingArchetype.POINT,
                use=BuildingUse.RESIDENTIAL,
                min_floors=floors,
                max_floors=floors,
            ),
        ),
    )


def _proposals(
    config: BuildingBenchmarkConfig,
) -> tuple[BuildingPlacementProposal, ...]:
    columns = math.ceil(math.sqrt(config.building_count))
    proposals: list[BuildingPlacementProposal] = []
    for index in range(config.building_count):
        row, column = divmod(index, columns)
        x = column * config.cell_size_m
        y = row * config.cell_size_m
        proposals.append(
            BuildingPlacementProposal(
                proposal_id=f"building:{index:05d}",
                source_id="parcel:reference",
                geometry=box(
                    x,
                    y,
                    x + config.footprint_size_m,
                    y + config.footprint_size_m,
                ),
                working_srid=config.working_srid,
                planning_floor_area_multiplier=float(config.floors),
            )
        )
    return tuple(proposals)


def run_reference_building_benchmark(
    config: BuildingBenchmarkConfig,
) -> BuildingBenchmarkResult:
    proposals = _proposals(config)
    footprint_area = config.footprint_size_m**2
    site_area_m2 = config.building_count * config.cell_size_m**2
    target_coverage = config.building_count * footprint_area / site_area_m2
    target_far = target_coverage * config.floors

    converger = BuildingPlacementConverger(
        working_srid=config.working_srid,
        spacing_policy=BuildingSpacingPolicy(
            minimum_gap_m=(config.cell_size_m - config.footprint_size_m) / 2.0,
        ),
        max_proposals=config.building_count,
        max_iterations=config.building_count,
        max_spacing_footprints=config.building_count,
    )
    placement_started = perf_counter_ns()
    placement = converger.converge(
        proposals,
        targets=BuildingPlacementTargets(
            site_area_m2=site_area_m2,
            target_coverage_ratio=target_coverage,
            target_far=target_far,
            coverage_tolerance=0.0,
            far_tolerance=0.0,
        ),
    )
    placement_finished = perf_counter_ns()
    if placement.diagnostics.accepted_count != config.building_count:
        raise RuntimeError(
            "reference placement accepted count changed: "
            f"{placement.diagnostics.accepted_count} != {config.building_count}"
        )
    if not placement.diagnostics.targets_met:
        raise RuntimeError("reference placement no longer reaches target window")

    attribute_subjects = tuple(
        BuildingAttributeSubject(
            building_id=proposal.proposal_id,
            source_id=proposal.source_id,
            zone_class=ZoneClass.RESIDENTIAL,
            archetype=BuildingArchetype.POINT,
        )
        for proposal in placement.accepted
    )
    assignment_started = perf_counter_ns()
    assignment = BuildingAttributeAssigner(
        max_subjects=config.building_count,
    ).assign(
        attribute_subjects,
        config=_attribute_config(config.floors),
        context=_context(),
    )
    assignment_finished = perf_counter_ns()

    by_id = {item.building_id: item for item in assignment.buildings}
    area_subjects = tuple(
        BuildingAreaSubject(
            building_id=proposal.proposal_id,
            geometry=proposal.geometry,
            attributes=by_id[proposal.proposal_id],
            working_srid=config.working_srid,
        )
        for proposal in placement.accepted
    )
    metrics_started = perf_counter_ns()
    metrics = BuildingAreaMetricsCalculator(
        working_srid=config.working_srid,
        max_subjects=config.building_count,
    ).calculate(
        area_subjects,
        site_area_m2=site_area_m2,
    )
    metrics_finished = perf_counter_ns()

    if not math.isclose(
        metrics.summary.coverage_ratio,
        target_coverage,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise RuntimeError("authoritative coverage diverged from planning target")
    if not math.isclose(
        metrics.summary.far,
        target_far,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise RuntimeError("authoritative FAR diverged from aligned planning target")

    digest = hashlib.sha256()
    for subject in area_subjects:
        digest.update(subject.building_id.encode("utf-8"))
        digest.update(subject.geometry.wkb)
        digest.update(str(subject.attributes.floors).encode("ascii"))

    return BuildingBenchmarkResult(
        fixture_name=REFERENCE_FIXTURE_NAME,
        building_count=config.building_count,
        working_srid=config.working_srid,
        target_coverage_ratio=target_coverage,
        target_far=target_far,
        accepted_count=placement.diagnostics.accepted_count,
        placement_ms=_elapsed_ms(placement_started, placement_finished),
        attribute_assignment_ms=_elapsed_ms(
            assignment_started,
            assignment_finished,
        ),
        area_metrics_ms=_elapsed_ms(metrics_started, metrics_finished),
        final_coverage_ratio=metrics.summary.coverage_ratio,
        final_far=metrics.summary.far,
        total_footprint_area_m2=metrics.summary.total_footprint_area_m2,
        total_gfa_m2=metrics.summary.total_gfa_m2,
        deterministic_digest=digest.hexdigest(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic S08 building reference workloads."
    )
    parser.add_argument(
        "--building-counts",
        type=int,
        nargs="+",
        default=(1000, 10000),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    results = [
        run_reference_building_benchmark(
            BuildingBenchmarkConfig(building_count=count)
        )
        for count in args.building_counts
    ]
    print(
        json.dumps(
            [asdict(result) for result in results],
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
