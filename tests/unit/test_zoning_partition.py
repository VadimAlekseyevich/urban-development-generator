from __future__ import annotations

import pytest
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from core.urban_generator.domain import CRSContractError
from core.urban_generator.zoning import (
    BaseZoningPartitioner,
    ZoningPartitionError,
    ZoningSeed,
    ZoningSeedSet,
)


def make_seed_set(*coordinates: tuple[float, float]) -> ZoningSeedSet:
    seeds = tuple(
        ZoningSeed(
            row=index,
            col=index,
            x_m=x_m,
            y_m=y_m,
            suitability_score=1.0,
        )
        for index, (x_m, y_m) in enumerate(coordinates)
    )
    return ZoningSeedSet(
        seeds=seeds,
        rng_namespace="zoning.seeds.v1",
        rng_seed=42,
        weighted_seed_count=len(seeds),
        uniform_fallback_count=0,
    )


def test_two_seeds_partition_rectangle_at_voronoi_bisector() -> None:
    developable = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    seeds = make_seed_set((25.0, 50.0), (75.0, 50.0))

    result = BaseZoningPartitioner().partition(
        seeds=seeds,
        developable_area=developable,
        working_srid=32637,
    )

    assert len(result.cells) == 2
    assert result.cells[0].seed == seeds.seeds[0]
    assert result.cells[1].seed == seeds.seeds[1]
    assert result.cells[0].area_m2 == pytest.approx(5_000.0)
    assert result.cells[1].area_m2 == pytest.approx(5_000.0)
    assert result.cells[0].geometry.bounds[2] == pytest.approx(50.0)
    assert result.cells[1].geometry.bounds[0] == pytest.approx(50.0)
    assert result.coverage_ratio == pytest.approx(1.0)
    assert result.uncovered_area_m2 == pytest.approx(0.0)
    assert result.overlap_area_m2 == pytest.approx(0.0)


def test_partition_is_clipped_to_concave_developable_area_and_covers_it() -> None:
    developable = Polygon(
        [(0, 0), (100, 0), (100, 40), (60, 40), (60, 100), (0, 100)]
    )
    seeds = make_seed_set((20.0, 20.0), (80.0, 20.0), (30.0, 80.0))

    result = BaseZoningPartitioner().partition(
        seeds=seeds,
        developable_area=developable,
        working_srid=32637,
    )

    partition_union = unary_union(tuple(cell.geometry for cell in result.cells))
    assert partition_union.symmetric_difference(developable).area == pytest.approx(0.0)
    assert all(developable.covers(cell.geometry) for cell in result.cells)
    assert sum(cell.area_m2 for cell in result.cells) == pytest.approx(developable.area)
    assert all(cell.geometry.is_valid for cell in result.cells)
    assert all(
        cell.geometry.covers(Point(cell.seed.x_m, cell.seed.y_m)) for cell in result.cells
    )


def test_partition_preserves_developable_hole() -> None:
    developable = Polygon(
        shell=[(0, 0), (100, 0), (100, 100), (0, 100)],
        holes=[[(40, 40), (60, 40), (60, 60), (40, 60)]],
    )
    hole = Polygon([(40, 40), (60, 40), (60, 60), (40, 60)])
    seeds = make_seed_set((25.0, 25.0), (75.0, 25.0), (25.0, 75.0), (75.0, 75.0))

    result = BaseZoningPartitioner().partition(
        seeds=seeds,
        developable_area=developable,
        working_srid=32637,
    )

    assert unary_union(tuple(cell.geometry for cell in result.cells)).area == pytest.approx(
        developable.area
    )
    assert all(cell.geometry.intersection(hole).area == pytest.approx(0.0) for cell in result.cells)


def test_invalid_developable_polygon_is_repaired_before_partition() -> None:
    bow_tie = Polygon([(0, 0), (10, 10), (0, 10), (10, 0), (0, 0)])
    assert not bow_tie.is_valid
    seeds = make_seed_set((5.0, 2.0), (5.0, 8.0))

    result = BaseZoningPartitioner().partition(
        seeds=seeds,
        developable_area=bow_tie,
        working_srid=32637,
    )

    assert result.developable_validity_repaired
    assert result.developable_area.is_valid
    assert result.developable_area.geom_type in {"Polygon", "MultiPolygon"}
    assert all(cell.geometry.is_valid for cell in result.cells)
    assert result.coverage_ratio == pytest.approx(1.0)


def test_single_seed_owns_entire_developable_area() -> None:
    developable = Polygon([(0, 0), (40, 0), (40, 20), (0, 20)])
    seeds = make_seed_set((10.0, 10.0))

    result = BaseZoningPartitioner().partition(
        seeds=seeds,
        developable_area=developable,
        working_srid=32637,
    )

    assert len(result.cells) == 1
    assert result.cells[0].geometry.equals(developable)
    assert result.cells[0].area_m2 == pytest.approx(800.0)


def test_partition_is_deterministic_for_same_seed_order_and_geometry() -> None:
    developable = Polygon([(0, 0), (120, 0), (120, 90), (0, 90)])
    seeds = make_seed_set((20.0, 20.0), (90.0, 20.0), (30.0, 70.0), (95.0, 65.0))
    partitioner = BaseZoningPartitioner()

    first = partitioner.partition(
        seeds=seeds,
        developable_area=developable,
        working_srid=32637,
    )
    second = partitioner.partition(
        seeds=seeds,
        developable_area=developable,
        working_srid=32637,
    )

    assert tuple(cell.seed_index for cell in first.cells) == tuple(
        cell.seed_index for cell in second.cells
    )
    assert tuple(cell.geometry.wkb for cell in first.cells) == tuple(
        cell.geometry.wkb for cell in second.cells
    )
    assert first.covered_area_m2 == second.covered_area_m2
    assert first.overlap_area_m2 == second.overlap_area_m2


def test_seed_outside_developable_area_is_rejected() -> None:
    developable = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    seeds = make_seed_set((5.0, 5.0), (20.0, 5.0))

    with pytest.raises(ZoningPartitionError, match="must be covered"):
        BaseZoningPartitioner().partition(
            seeds=seeds,
            developable_area=developable,
            working_srid=32637,
        )


def test_duplicate_seed_coordinates_are_rejected() -> None:
    developable = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    seeds = make_seed_set((5.0, 5.0), (5.0, 5.0))

    with pytest.raises(ZoningPartitionError, match="coordinates must be unique"):
        BaseZoningPartitioner().partition(
            seeds=seeds,
            developable_area=developable,
            working_srid=32637,
        )


def test_partition_requires_metric_working_crs() -> None:
    developable = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    seeds = make_seed_set((5.0, 5.0))

    with pytest.raises(CRSContractError):
        BaseZoningPartitioner().partition(
            seeds=seeds,
            developable_area=developable,
            working_srid=4326,
        )
