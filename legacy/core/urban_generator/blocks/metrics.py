from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon

from core.urban_generator.blocks.developable_clipping import (
    BlockDevelopableClippingResult,
    DevelopableBlockCandidate,
)
from core.urban_generator.domain.crs import WorkingCRS, require_working_crs

DEFAULT_MAX_METRIC_BLOCKS = 500_000


class BlockMetricsError(ValueError):
    """Raised when block metric inputs violate the bounded metric-CRS contract."""


@dataclass(frozen=True, slots=True)
class BlockMetrics:
    """Raw metric-CRS measurements for one developable block polygon.

    ``perimeter_m`` is the total polygon boundary length, including interior rings. Compactness
    is Polsby-Popper ``4*pi*area/perimeter^2`` and therefore remains a raw geometric metric rather
    than a composite score. ``aspect_ratio`` is the long/short side ratio of the minimum rotated
    rectangle and is always at least 1 for a valid positive-area polygon.
    """

    area_m2: float
    perimeter_m: float
    compactness: float
    aspect_ratio: float
    hole_count: int

    def __post_init__(self) -> None:
        _require_positive_finite("area_m2", self.area_m2)
        _require_positive_finite("perimeter_m", self.perimeter_m)
        _require_finite_range("compactness", self.compactness, minimum=0.0, maximum=1.0)
        if self.compactness <= 0.0:
            raise BlockMetricsError("compactness must be positive")
        _require_positive_finite("aspect_ratio", self.aspect_ratio)
        if self.aspect_ratio < 1.0:
            raise BlockMetricsError("aspect_ratio must be at least 1")
        if (
            isinstance(self.hole_count, bool)
            or not isinstance(self.hole_count, int)
            or self.hole_count < 0
        ):
            raise BlockMetricsError("hole_count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class MeasuredBlock:
    """One immutable T02 block candidate paired with its raw T03 metrics."""

    block: DevelopableBlockCandidate
    metrics: BlockMetrics

    def __post_init__(self) -> None:
        if not isinstance(self.block, DevelopableBlockCandidate):
            raise BlockMetricsError("block must be a DevelopableBlockCandidate")
        if not isinstance(self.metrics, BlockMetrics):
            raise BlockMetricsError("metrics must be a BlockMetrics value")


@dataclass(frozen=True, slots=True)
class BlockMetricsDiagnostics:
    """Aggregate diagnostics for one bounded metric calculation pass."""

    block_count: int
    total_area_m2: float
    total_perimeter_m: float
    block_with_holes_count: int
    total_hole_count: int

    def __post_init__(self) -> None:
        _require_non_negative_int("block_count", self.block_count)
        _require_non_negative_finite("total_area_m2", self.total_area_m2)
        _require_non_negative_finite("total_perimeter_m", self.total_perimeter_m)
        _require_non_negative_int("block_with_holes_count", self.block_with_holes_count)
        _require_non_negative_int("total_hole_count", self.total_hole_count)
        if self.block_with_holes_count > self.block_count:
            raise BlockMetricsError("block_with_holes_count cannot exceed block_count")


@dataclass(frozen=True, slots=True)
class BlockMetricsResult:
    """Deterministic raw block metrics ready for later S07 validation/splitting stages."""

    working_crs: WorkingCRS
    blocks: tuple[MeasuredBlock, ...]
    diagnostics: BlockMetricsDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.working_crs, WorkingCRS):
            raise BlockMetricsError("working_crs must be a WorkingCRS")
        if not isinstance(self.blocks, tuple):
            raise BlockMetricsError("blocks must be an immutable tuple")
        if any(not isinstance(block, MeasuredBlock) for block in self.blocks):
            raise BlockMetricsError("blocks must contain only MeasuredBlock values")
        if not isinstance(self.diagnostics, BlockMetricsDiagnostics):
            raise BlockMetricsError("diagnostics must be a BlockMetricsDiagnostics value")
        if self.diagnostics.block_count != len(self.blocks):
            raise BlockMetricsError("diagnostics block_count must match result blocks")


class BlockMetricsCalculator:
    """Calculate bounded deterministic geometry metrics for S07-T02 block candidates.

    This class performs no frontage/access validation, oversized-block classification or split,
    sliver cleanup, zone association, persistence, API, or UI work. It consumes only the immutable
    T02 result and keeps the original candidate geometry/provenance attached to every metric row.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        max_blocks: int = DEFAULT_MAX_METRIC_BLOCKS,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        _require_positive_int("max_blocks", max_blocks)
        self.max_blocks = max_blocks

    def calculate(self, clipping: BlockDevelopableClippingResult) -> BlockMetricsResult:
        if not isinstance(clipping, BlockDevelopableClippingResult):
            raise BlockMetricsError("clipping must be a BlockDevelopableClippingResult")
        if clipping.working_crs != self.working_crs:
            raise BlockMetricsError("clipping working CRS must match calculator working CRS")
        if len(clipping.blocks) > self.max_blocks:
            raise BlockMetricsError(
                f"block metric input limit exceeded: {len(clipping.blocks)} > {self.max_blocks}"
            )

        seen_ids: set[str] = set()
        measured: list[MeasuredBlock] = []
        for block in sorted(clipping.blocks, key=lambda item: item.block_id):
            if block.block_id in seen_ids:
                raise BlockMetricsError(f"duplicate block_id: {block.block_id!r}")
            seen_ids.add(block.block_id)
            measured.append(MeasuredBlock(block=block, metrics=_measure(block.geometry)))

        blocks = tuple(measured)
        diagnostics = BlockMetricsDiagnostics(
            block_count=len(blocks),
            total_area_m2=math.fsum(item.metrics.area_m2 for item in blocks),
            total_perimeter_m=math.fsum(item.metrics.perimeter_m for item in blocks),
            block_with_holes_count=sum(item.metrics.hole_count > 0 for item in blocks),
            total_hole_count=sum(item.metrics.hole_count for item in blocks),
        )
        return BlockMetricsResult(
            working_crs=self.working_crs,
            blocks=blocks,
            diagnostics=diagnostics,
        )


def _measure(geometry: Polygon) -> BlockMetrics:
    if not isinstance(geometry, Polygon):
        raise BlockMetricsError("block geometry must be a Polygon")
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise BlockMetricsError("block geometry must be non-empty, valid and 2D")

    area_m2 = float(geometry.area)
    perimeter_m = float(geometry.length)
    _require_positive_finite("block area", area_m2)
    _require_positive_finite("block perimeter", perimeter_m)

    compactness = (4.0 * math.pi * area_m2) / (perimeter_m * perimeter_m)
    if not math.isfinite(compactness) or compactness <= 0.0:
        raise BlockMetricsError("block compactness must be positive and finite")
    # Floating-point overlay/length arithmetic can exceed the theoretical bound by a few ulps.
    compactness = min(1.0, compactness)

    rectangle = geometry.minimum_rotated_rectangle
    if not isinstance(rectangle, Polygon) or rectangle.is_empty:
        raise BlockMetricsError("minimum rotated rectangle must be a non-empty Polygon")
    coordinates = tuple(rectangle.exterior.coords)
    if len(coordinates) != 5:
        raise BlockMetricsError("minimum rotated rectangle must have four sides")
    side_lengths = tuple(
        math.hypot(
            float(right[0]) - float(left[0]),
            float(right[1]) - float(left[1]),
        )
        for left, right in zip(coordinates[:-1], coordinates[1:], strict=True)
    )
    if any(not math.isfinite(length) or length <= 0.0 for length in side_lengths):
        raise BlockMetricsError("minimum rotated rectangle sides must be positive and finite")
    short_side_m = min(side_lengths)
    long_side_m = max(side_lengths)
    aspect_ratio = long_side_m / short_side_m

    return BlockMetrics(
        area_m2=area_m2,
        perimeter_m=perimeter_m,
        compactness=compactness,
        aspect_ratio=aspect_ratio,
        hole_count=len(geometry.interiors),
    )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BlockMetricsError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BlockMetricsError(f"{field_name} must be a non-negative integer")


def _require_positive_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockMetricsError(f"{field_name} must be a positive finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise BlockMetricsError(f"{field_name} must be a positive finite number")


def _require_non_negative_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockMetricsError(f"{field_name} must be a finite non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise BlockMetricsError(f"{field_name} must be a finite non-negative number")


def _require_finite_range(
    field_name: str,
    value: float,
    *,
    minimum: float,
    maximum: float,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockMetricsError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise BlockMetricsError(
            f"{field_name} must be between {minimum} and {maximum} inclusive"
        )
