from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from time import perf_counter_ns

from shapely.geometry import LineString, box

from core.urban_generator.blocks import (
    BlockZoneAssociator,
    BlockZoneReference,
    CleanedBlockCandidate,
    ParcelSubdivisionPolicy,
    SimplifiedParcelSubdivider,
    SliverCleanupDiagnostics,
    SliverCleanupPolicy,
    SliverCleanupResult,
    SplitBlockCandidate,
)
from core.urban_generator.domain import WorkingCRS, WorldStateContract
from core.urban_generator.roads import NodedRoad, RoadGraph, RoadGraphBuilder, RoadGraphInput
from core.urban_generator.zoning import ZoneClass

REFERENCE_FIXTURE_NAME = "block-parcel-grid-v1"


@dataclass(frozen=True, slots=True)
class BlockParcelBenchmarkConfig:
    """Deterministic block/parcel workload for spatial-index regression checks."""

    grid_size: int = 24
    block_width_m: float = 40.0
    block_height_m: float = 20.0
    column_spacing_m: float = 60.0
    row_spacing_m: float = 40.0
    working_srid: int = 3857

    def __post_init__(self) -> None:
        if isinstance(self.grid_size, bool) or not isinstance(self.grid_size, int):
            raise ValueError("grid_size must be an integer")
        if self.grid_size < 2:
            raise ValueError("grid_size must be at least 2")
        for name, value in (
            ("block_width_m", self.block_width_m),
            ("block_height_m", self.block_height_m),
            ("column_spacing_m", self.column_spacing_m),
            ("row_spacing_m", self.row_spacing_m),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be positive and finite")
        if self.column_spacing_m <= self.block_width_m:
            raise ValueError("column_spacing_m must exceed block_width_m")
        if self.row_spacing_m <= self.block_height_m:
            raise ValueError("row_spacing_m must exceed block_height_m")
        _ = WorkingCRS(self.working_srid)


REFERENCE_CONFIG = BlockParcelBenchmarkConfig()


@dataclass(frozen=True, slots=True)
class BlockParcelBenchmarkFixture:
    config: BlockParcelBenchmarkConfig
    cleanup: SliverCleanupResult
    zones: tuple[BlockZoneReference, ...]
    road_graph: RoadGraph


@dataclass(frozen=True, slots=True)
class BlockParcelBenchmarkResult:
    fixture_name: str
    working_srid: int
    grid_size: int
    block_count: int
    zone_count: int
    road_edge_count: int
    parcel_count: int
    zone_association_ms: float
    parcel_subdivision_ms: float
    zone_spatial_candidate_pair_count: int
    zone_positive_overlap_pair_count: int
    parcel_road_candidate_pair_count: int
    parcel_frontage_overlap_pair_count: int
    parceled_area_m2: float


def _elapsed_ms(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1_000_000.0


def _block_id(row: int, column: int) -> str:
    return f"block:{row:03d}:{column:03d}"


def build_reference_fixture(
    config: BlockParcelBenchmarkConfig = REFERENCE_CONFIG,
) -> BlockParcelBenchmarkFixture:
    """Build isolated residential blocks with exactly one zone/frontage road each."""

    blocks: list[CleanedBlockCandidate] = []
    zones: list[BlockZoneReference] = []
    roads: list[RoadGraphInput] = []

    for row in range(config.grid_size):
        for column in range(config.grid_size):
            x = column * config.column_spacing_m
            y = row * config.row_spacing_m
            geometry = box(
                x,
                y,
                x + config.block_width_m,
                y + config.block_height_m,
            )
            block_id = _block_id(row, column)
            member = SplitBlockCandidate(
                block_id=f"split:{block_id}",
                input_block_id=f"input:{block_id}",
                source_block_id=f"source:{block_id}",
                source_fragment_index=0,
                split_path=(),
                geometry=geometry,
            )
            blocks.append(
                CleanedBlockCandidate(
                    block_id=block_id,
                    members=(member,),
                    geometry=geometry,
                )
            )
            zones.append(
                BlockZoneReference(
                    zone_id=f"zone:{row:03d}:{column:03d}",
                    zone_class=ZoneClass.RESIDENTIAL,
                    geometry=geometry,
                    working_srid=config.working_srid,
                )
            )
            road_id = f"road:{row:03d}:{column:03d}"
            roads.append(
                RoadGraphInput(
                    road=NodedRoad(
                        road_id=road_id,
                        parts=(
                            LineString(
                                (
                                    (x, y),
                                    (x + config.block_width_m, y),
                                )
                            ),
                        ),
                    ),
                    state=WorldStateContract.fixed_source(),
                )
            )

    ordered_blocks = tuple(sorted(blocks, key=lambda block: block.block_id))
    block_area_m2 = config.block_width_m * config.block_height_m
    total_area_m2 = len(ordered_blocks) * block_area_m2
    cleanup = SliverCleanupResult(
        working_crs=WorkingCRS(srid=config.working_srid),
        policy=SliverCleanupPolicy(min_area_m2=1.0),
        blocks=ordered_blocks,
        events=(),
        diagnostics=SliverCleanupDiagnostics(
            input_block_count=len(ordered_blocks),
            input_sliver_count=0,
            output_block_count=len(ordered_blocks),
            merged_event_count=0,
            dropped_event_count=0,
            kept_unresolved_event_count=0,
            operation_count=0,
            spatial_candidate_pair_count=0,
            adjacency_pair_count=0,
            input_area_m2=total_area_m2,
            output_area_m2=total_area_m2,
            dropped_area_m2=0.0,
        ),
    )
    road_graph = RoadGraphBuilder(working_srid=config.working_srid).build(
        tuple(roads)
    )
    return BlockParcelBenchmarkFixture(
        config=config,
        cleanup=cleanup,
        zones=tuple(sorted(zones, key=lambda zone: zone.zone_id)),
        road_graph=road_graph,
    )


def run_reference_block_parcel_benchmark(
    config: BlockParcelBenchmarkConfig = REFERENCE_CONFIG,
) -> BlockParcelBenchmarkResult:
    """Run indexed zone association and simplified parcel subdivision."""

    fixture = build_reference_fixture(config)
    expected_blocks = config.grid_size * config.grid_size

    associator = BlockZoneAssociator(
        working_srid=config.working_srid,
        max_blocks=expected_blocks,
        max_zones=expected_blocks,
        max_candidates_per_block=4,
    )
    association_started = perf_counter_ns()
    zoned = associator.associate(fixture.cleanup, zones=fixture.zones)
    association_finished = perf_counter_ns()

    if zoned.diagnostics.associated_block_count != expected_blocks:
        raise RuntimeError(
            "reference zone association count changed: "
            f"{zoned.diagnostics.associated_block_count} != {expected_blocks}"
        )
    if zoned.diagnostics.spatial_candidate_pair_count != expected_blocks:
        raise RuntimeError(
            "reference zone candidate count changed: "
            f"{zoned.diagnostics.spatial_candidate_pair_count} != {expected_blocks}"
        )

    subdivider = SimplifiedParcelSubdivider(
        working_srid=config.working_srid,
        policy=ParcelSubdivisionPolicy(
            target_frontage_m=10.0,
            minimum_frontage_m=8.0,
            minimum_parcel_area_m2=100.0,
        ),
        max_blocks=expected_blocks,
        max_road_edges=expected_blocks,
        max_road_candidates_per_block=4,
        max_output_parcels=expected_blocks * 4,
    )
    subdivision_started = perf_counter_ns()
    subdivision = subdivider.subdivide(zoned, road_graph=fixture.road_graph)
    subdivision_finished = perf_counter_ns()

    expected_parcels = expected_blocks * 4
    if subdivision.diagnostics.parceled_block_count != expected_blocks:
        raise RuntimeError(
            "reference parceled block count changed: "
            f"{subdivision.diagnostics.parceled_block_count} != {expected_blocks}"
        )
    if subdivision.diagnostics.parcel_count != expected_parcels:
        raise RuntimeError(
            "reference parcel count changed: "
            f"{subdivision.diagnostics.parcel_count} != {expected_parcels}"
        )
    if subdivision.diagnostics.road_candidate_pair_count != expected_blocks:
        raise RuntimeError(
            "reference road candidate count changed: "
            f"{subdivision.diagnostics.road_candidate_pair_count} != {expected_blocks}"
        )

    return BlockParcelBenchmarkResult(
        fixture_name=REFERENCE_FIXTURE_NAME,
        working_srid=config.working_srid,
        grid_size=config.grid_size,
        block_count=expected_blocks,
        zone_count=len(fixture.zones),
        road_edge_count=len(fixture.road_graph.edges),
        parcel_count=len(subdivision.parcels),
        zone_association_ms=_elapsed_ms(
            association_started,
            association_finished,
        ),
        parcel_subdivision_ms=_elapsed_ms(
            subdivision_started,
            subdivision_finished,
        ),
        zone_spatial_candidate_pair_count=(
            zoned.diagnostics.spatial_candidate_pair_count
        ),
        zone_positive_overlap_pair_count=(
            zoned.diagnostics.positive_overlap_pair_count
        ),
        parcel_road_candidate_pair_count=(
            subdivision.diagnostics.road_candidate_pair_count
        ),
        parcel_frontage_overlap_pair_count=(
            subdivision.diagnostics.frontage_overlap_pair_count
        ),
        parceled_area_m2=subdivision.diagnostics.parceled_area_m2,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic S07 block/parcel reference benchmark."
    )
    parser.add_argument("--grid-size", type=int, default=REFERENCE_CONFIG.grid_size)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = run_reference_block_parcel_benchmark(
        BlockParcelBenchmarkConfig(grid_size=args.grid_size)
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
