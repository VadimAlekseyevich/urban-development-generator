import math

import pytest
from shapely.affinity import rotate
from shapely.geometry import Polygon, box

from core.urban_generator.blocks import (
    BlockDevelopableClippingDiagnostics,
    BlockDevelopableClippingResult,
    BlockMetricsCalculator,
    BlockMetricsError,
    DevelopableBlockCandidate,
)
from core.urban_generator.domain import WorkingCRS
from core.urban_generator.domain.crs import CRSContractError

WORKING_SRID = 3857


def _candidate(
    block_id: str,
    geometry: Polygon,
    *,
    source_block_id: str | None = None,
    source_fragment_index: int = 0,
) -> DevelopableBlockCandidate:
    return DevelopableBlockCandidate(
        block_id=block_id,
        source_block_id=source_block_id or f"source:{block_id}",
        source_fragment_index=source_fragment_index,
        geometry=geometry,
    )


def _clipping(
    *blocks: DevelopableBlockCandidate,
    working_srid: int = WORKING_SRID,
) -> BlockDevelopableClippingResult:
    return BlockDevelopableClippingResult(
        working_crs=WorkingCRS(srid=working_srid),
        blocks=tuple(blocks),
        diagnostics=BlockDevelopableClippingDiagnostics(
            input_block_count=len(blocks),
            output_block_count=len(blocks),
            mask_clipped_block_count=0,
            mask_removed_block_count=0,
            hard_constraint_hit_block_count=0,
            hard_constraint_removed_block_count=0,
            split_source_block_count=0,
            hard_constraint_geometry_count=0,
            constraint_candidate_count=0,
            validity_repair_count=0,
        ),
    )


def test_rectangle_metrics_use_metric_geometry_and_polsby_popper_compactness() -> None:
    block = _candidate("block:rectangle", box(0, 0, 10, 5))

    result = BlockMetricsCalculator(working_srid=WORKING_SRID).calculate(_clipping(block))

    assert len(result.blocks) == 1
    measured = result.blocks[0]
    assert measured.block == block
    assert measured.metrics.area_m2 == pytest.approx(50.0)
    assert measured.metrics.perimeter_m == pytest.approx(30.0)
    assert measured.metrics.compactness == pytest.approx(2.0 * math.pi / 9.0)
    assert measured.metrics.aspect_ratio == pytest.approx(2.0)
    assert measured.metrics.hole_count == 0


def test_perimeter_and_compactness_include_interior_rings() -> None:
    geometry = Polygon(
        shell=((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)),
        holes=(((4, 4), (6, 4), (6, 6), (4, 6), (4, 4)),),
    )

    result = BlockMetricsCalculator(working_srid=WORKING_SRID).calculate(
        _clipping(_candidate("block:hole", geometry))
    )

    metrics = result.blocks[0].metrics
    assert metrics.area_m2 == pytest.approx(96.0)
    assert metrics.perimeter_m == pytest.approx(48.0)
    assert metrics.compactness == pytest.approx(math.pi / 6.0)
    assert metrics.aspect_ratio == pytest.approx(1.0)
    assert metrics.hole_count == 1
    assert result.diagnostics.block_with_holes_count == 1
    assert result.diagnostics.total_hole_count == 1


def test_minimum_rotated_rectangle_aspect_ratio_is_rotation_invariant() -> None:
    rectangle = box(0, 0, 12, 3)
    rotated = rotate(rectangle, 31.0, origin="centroid")
    assert isinstance(rotated, Polygon)

    result = BlockMetricsCalculator(working_srid=WORKING_SRID).calculate(
        _clipping(_candidate("block:rotated", rotated))
    )

    assert result.blocks[0].metrics.area_m2 == pytest.approx(36.0)
    assert result.blocks[0].metrics.aspect_ratio == pytest.approx(4.0)


def test_aggregate_diagnostics_sum_raw_block_metrics() -> None:
    first = _candidate("block:a", box(0, 0, 4, 4))
    second = _candidate("block:b", box(10, 0, 12, 3))

    result = BlockMetricsCalculator(working_srid=WORKING_SRID).calculate(
        _clipping(first, second)
    )

    assert result.diagnostics.block_count == 2
    assert result.diagnostics.total_area_m2 == pytest.approx(22.0)
    assert result.diagnostics.total_perimeter_m == pytest.approx(26.0)
    assert result.diagnostics.block_with_holes_count == 0
    assert result.diagnostics.total_hole_count == 0


def test_results_are_deterministic_and_sorted_by_block_id() -> None:
    alpha = _candidate("block:a", box(0, 0, 2, 3))
    beta = _candidate("block:b", box(10, 0, 14, 2))
    calculator = BlockMetricsCalculator(working_srid=WORKING_SRID)

    forward = calculator.calculate(_clipping(alpha, beta))
    reversed_input = calculator.calculate(_clipping(beta, alpha))

    assert forward == reversed_input
    assert tuple(item.block.block_id for item in forward.blocks) == ("block:a", "block:b")


def test_empty_clipping_result_produces_zero_diagnostics() -> None:
    result = BlockMetricsCalculator(working_srid=WORKING_SRID).calculate(_clipping())

    assert result.blocks == ()
    assert result.diagnostics.block_count == 0
    assert result.diagnostics.total_area_m2 == 0.0
    assert result.diagnostics.total_perimeter_m == 0.0
    assert result.diagnostics.block_with_holes_count == 0
    assert result.diagnostics.total_hole_count == 0


def test_calculator_requires_matching_metric_crs() -> None:
    calculator = BlockMetricsCalculator(working_srid=WORKING_SRID)
    block = _candidate("block:a", box(0, 0, 1, 1))

    with pytest.raises(BlockMetricsError, match="working CRS must match"):
        calculator.calculate(_clipping(block, working_srid=3395))

    with pytest.raises(CRSContractError, match="not projected"):
        BlockMetricsCalculator(working_srid=4326)


def test_calculator_enforces_configured_block_bound() -> None:
    clipping = _clipping(
        _candidate("block:a", box(0, 0, 1, 1)),
        _candidate("block:b", box(2, 0, 3, 1)),
    )

    with pytest.raises(BlockMetricsError, match="block metric input limit exceeded: 2 > 1"):
        BlockMetricsCalculator(working_srid=WORKING_SRID, max_blocks=1).calculate(clipping)


def test_duplicate_block_ids_are_rejected_instead_of_silently_overwriting_metrics() -> None:
    clipping = _clipping(
        _candidate("block:duplicate", box(0, 0, 1, 1), source_block_id="source:a"),
        _candidate("block:duplicate", box(2, 0, 3, 1), source_block_id="source:b"),
    )

    with pytest.raises(BlockMetricsError, match="duplicate block_id"):
        BlockMetricsCalculator(working_srid=WORKING_SRID).calculate(clipping)
