from __future__ import annotations

import math
import random

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from core.urban_generator.domain import SnapshotLayerKind, SnapshotLayerRef
from core.urban_generator.zoning import (
    BaseZoningPartitioner,
    BoundedRegionRefiner,
    FixedExistingZonesAdapter,
    SuitabilityTargetShareAssigner,
    ZoneAdjacencyPolicy,
    ZoneAdjacencyRule,
    ZoneClass,
    ZoneClassConfig,
    ZoneRefinementResult,
    ZoningConfig,
    ZoningPartitionCell,
    ZoningPartitionResult,
    ZoningSeed,
    ZoningSeedSet,
)

WORKING_SRID = 32637


def _seed_set(
    coordinates: tuple[tuple[float, float], ...],
    *,
    score_offset: int = 0,
) -> ZoningSeedSet:
    seeds = tuple(
        ZoningSeed(
            row=index,
            col=index,
            x_m=x_m,
            y_m=y_m,
            suitability_score=((index * 37 + score_offset * 11) % 101) / 100.0,
        )
        for index, (x_m, y_m) in enumerate(coordinates)
    )
    return ZoningSeedSet(
        seeds=seeds,
        rng_namespace="zoning.property-tests",
        rng_seed=score_offset,
        weighted_seed_count=len(seeds),
        uniform_fallback_count=0,
    )


def _config(
    shares: tuple[float, float, float, float] = (0.4, 0.3, 0.2, 0.1),
    *,
    minimum_area_m2: float = 1.0,
    with_adjacency: bool = False,
) -> ZoningConfig:
    rules = (
        (
            ZoneAdjacencyRule(
                ZoneClass.RESIDENTIAL,
                ZoneClass.PUBLIC,
                ZoneAdjacencyPolicy.FORBIDDEN,
            ),
            ZoneAdjacencyRule(
                ZoneClass.MIXED,
                ZoneClass.RECREATION,
                ZoneAdjacencyPolicy.PREFERRED,
            ),
        )
        if with_adjacency
        else ()
    )
    return ZoningConfig(
        version="property-tests-v1",
        zones=tuple(
            ZoneClassConfig(zone_class, share, minimum_area_m2)
            for zone_class, share in zip(ZoneClass, shares, strict=True)
        ),
        adjacency_rules=rules,
    )


def _partition(
    developable: Polygon | MultiPolygon,
    coordinates: tuple[tuple[float, float], ...],
    *,
    score_offset: int = 0,
) -> ZoningPartitionResult:
    return BaseZoningPartitioner().partition(
        seeds=_seed_set(coordinates, score_offset=score_offset),
        developable_area=developable,
        working_srid=WORKING_SRID,
    )


def _assert_partition_properties(result: ZoningPartitionResult) -> None:
    tolerance_m2 = max(1e-6, result.developable_area_m2 * 1e-9)
    union = unary_union(tuple(cell.geometry for cell in result.cells))

    assert result.developable_area.is_valid
    assert result.coverage_ratio == pytest.approx(1.0, abs=1e-9)
    assert result.uncovered_area_m2 <= tolerance_m2
    assert result.overlap_area_m2 <= tolerance_m2
    assert union.symmetric_difference(result.developable_area).area <= tolerance_m2

    for cell in result.cells:
        assert cell.geometry.is_valid
        assert cell.geometry.geom_type in {"Polygon", "MultiPolygon"}
        assert result.developable_area.covers(cell.geometry)
        assert cell.geometry.covers(Point(cell.seed.x_m, cell.seed.y_m))

    for first_index, first in enumerate(result.cells):
        for second in result.cells[first_index + 1 :]:
            assert first.geometry.intersection(second.geometry).area <= tolerance_m2


@pytest.mark.parametrize("case_seed", range(12))
def test_partition_properties_hold_across_deterministic_generated_cases(case_seed: int) -> None:
    rng = random.Random(case_seed)
    width = 100.0 + case_seed * 7.0
    height = 80.0 + case_seed * 3.0
    developable = box(0.0, 0.0, width, height)
    count = 5 + case_seed % 8
    margin_x = width * 0.08
    margin_y = height * 0.08

    coordinates: list[tuple[float, float]] = []
    while len(coordinates) < count:
        candidate = (
            rng.uniform(margin_x, width - margin_x),
            rng.uniform(margin_y, height - margin_y),
        )
        if candidate not in coordinates:
            coordinates.append(candidate)

    result = _partition(
        developable,
        tuple(coordinates),
        score_offset=case_seed,
    )

    _assert_partition_properties(result)


@pytest.mark.parametrize(
    ("developable", "coordinates"),
    [
        (
            Polygon([(0, 0), (120, 0), (120, 45), (65, 45), (65, 110), (0, 110)]),
            ((20.0, 20.0), (90.0, 20.0), (25.0, 80.0), (55.0, 75.0)),
        ),
        (
            Polygon(
                shell=[(0, 0), (120, 0), (120, 120), (0, 120)],
                holes=[[(45, 45), (75, 45), (75, 75), (45, 75)]],
            ),
            ((25.0, 25.0), (95.0, 25.0), (25.0, 95.0), (95.0, 95.0)),
        ),
        (
            MultiPolygon([box(0.0, 0.0, 50.0, 70.0), box(90.0, 10.0, 150.0, 80.0)]),
            ((15.0, 20.0), (35.0, 55.0), (105.0, 25.0), (135.0, 60.0)),
        ),
    ],
)
def test_coverage_policy_preserves_concavity_holes_and_disconnected_components(
    developable: Polygon | MultiPolygon,
    coordinates: tuple[tuple[float, float], ...],
) -> None:
    result = _partition(developable, coordinates)

    _assert_partition_properties(result)


def _equal_strip_partition(cell_count: int) -> ZoningPartitionResult:
    cell_width = 10.0
    height = 10.0
    cells: list[ZoningPartitionCell] = []
    for index in range(cell_count):
        min_x = index * cell_width
        max_x = min_x + cell_width
        geometry = box(min_x, 0.0, max_x, height)
        seed = ZoningSeed(
            row=0,
            col=index,
            x_m=(min_x + max_x) / 2.0,
            y_m=height / 2.0,
            suitability_score=((index * 29) % cell_count) / max(1, cell_count - 1),
        )
        cells.append(
            ZoningPartitionCell(
                seed_index=index,
                seed=seed,
                geometry=geometry,
                area_m2=float(geometry.area),
                validity_repaired=False,
            )
        )

    developable = box(0.0, 0.0, cell_count * cell_width, height)
    return ZoningPartitionResult(
        working_srid=WORKING_SRID,
        developable_area=developable,
        cells=tuple(cells),
        developable_area_m2=float(developable.area),
        covered_area_m2=float(developable.area),
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=0,
    )


@pytest.mark.parametrize(
    "shares",
    [
        (0.4, 0.3, 0.2, 0.1),
        (0.25, 0.25, 0.25, 0.25),
        (0.55, 0.2, 0.15, 0.1),
        (0.37, 0.29, 0.21, 0.13),
        (0.6, 0.25, 0.15, 0.0),
    ],
)
def test_target_share_error_stays_within_one_indivisible_cell(
    shares: tuple[float, float, float, float],
) -> None:
    partition = _equal_strip_partition(40)
    config = _config(shares)
    assignment = SuitabilityTargetShareAssigner().assign(
        partition=partition,
        config=config,
    )
    refined = BoundedRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
        max_iterations=100,
    )
    largest_cell_m2 = max(cell.area_m2 for cell in partition.cells)

    for diagnostic in refined.assignment.shares:
        assert diagnostic.absolute_area_error_m2 <= largest_cell_m2 + 1e-9
    assert math.fsum(item.achieved_share for item in refined.assignment.shares) == pytest.approx(
        1.0
    )


@pytest.mark.parametrize("case_seed", range(6))
def test_zoning_pipeline_is_deterministic_across_generated_cases(case_seed: int) -> None:
    rng = random.Random(10_000 + case_seed)
    developable = box(0.0, 0.0, 140.0 + case_seed * 5.0, 100.0)
    coordinates = tuple(
        (rng.uniform(8.0, developable.bounds[2] - 8.0), rng.uniform(8.0, 92.0))
        for _index in range(10)
    )
    config = _config(minimum_area_m2=650.0, with_adjacency=True)

    def run_once() -> tuple[ZoningPartitionResult, ZoneRefinementResult]:
        partition = _partition(developable, coordinates, score_offset=case_seed)
        assignment = SuitabilityTargetShareAssigner().assign(
            partition=partition,
            config=config,
        )
        refinement = BoundedRegionRefiner().refine(
            partition=partition,
            assignment=assignment,
            config=config,
            max_iterations=50,
        )
        return partition, refinement

    first_partition, first_refinement = run_once()
    second_partition, second_refinement = run_once()

    assert tuple(cell.geometry.wkb for cell in first_partition.cells) == tuple(
        cell.geometry.wkb for cell in second_partition.cells
    )
    assert first_partition.covered_area_m2 == second_partition.covered_area_m2
    assert first_partition.overlap_area_m2 == second_partition.overlap_area_m2
    assert first_refinement == second_refinement


def test_expansion_gate_keeps_fixed_zone_refs_and_generates_only_developable_delta() -> None:
    source_layers = (
        SnapshotLayerRef(SnapshotLayerKind.LANDUSE, "dataset:existing-zoning:a"),
        SnapshotLayerRef(SnapshotLayerKind.LANDUSE, "dataset:existing-zoning:b"),
    )
    adapter = FixedExistingZonesAdapter()
    fixed_before = adapter.adapt(source_layers)
    source_before = tuple(source_layers)

    developable = Polygon([(0, 0), (100, 0), (100, 80), (55, 80), (55, 120), (0, 120)])
    partition = _partition(
        developable,
        ((20.0, 20.0), (75.0, 20.0), (20.0, 65.0), (45.0, 100.0)),
    )
    assignment = SuitabilityTargetShareAssigner().assign(
        partition=partition,
        config=_config(),
    )

    fixed_after = adapter.adapt(source_layers)
    generated_union = unary_union(tuple(cell.geometry for cell in partition.cells))

    assert source_layers == source_before
    assert fixed_after == fixed_before
    assert all(ref.state_contract.is_fixed_source for ref in fixed_after)
    assert generated_union.symmetric_difference(developable).area == pytest.approx(0.0)
    assert math.fsum(item.area_m2 for item in assignment.assignments) == pytest.approx(
        developable.area
    )
