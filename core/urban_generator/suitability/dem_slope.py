from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import numpy as np

from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot
from core.urban_generator.suitability.config import _validate_factor_code, _validate_version
from core.urban_generator.suitability.factors import (
    SuitabilityFactorError,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
)


class DEMSlopeError(SuitabilityFactorError):
    """Raised when DEM slope inputs violate the suitability factor contract."""


class DEMSlopeNoDataPolicy(StrEnum):
    """How missing neighbour elevations affect the output slope cell."""

    INVALIDATE = "INVALIDATE"
    ONE_SIDED = "ONE_SIDED"


@dataclass(frozen=True, slots=True)
class DEMSlopeWindow:
    """One bounded DEM read window expressed in target-grid pixel coordinates."""

    row_off: int
    col_off: int
    height: int
    width: int

    def __post_init__(self) -> None:
        for field_name in ("row_off", "col_off", "height", "width"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise DEMSlopeError(f"{field_name} must be an integer")
        if self.row_off < 0 or self.col_off < 0:
            raise DEMSlopeError("DEM window offsets must be non-negative")
        if self.height <= 0 or self.width <= 0:
            raise DEMSlopeError("DEM window dimensions must be positive")

    @property
    def cell_count(self) -> int:
        return self.height * self.width


class DEMSlopeSource(Protocol):
    """Minimal aligned, windowed elevation source required by the core slope factor."""

    grid: SuitabilityGridSpec

    def read_window(
        self,
        *,
        window: DEMSlopeWindow,
    ) -> tuple[tuple[float | int | None, ...], ...]:
        """Return exactly ``height x width`` elevations, mapping nodata to ``None``."""


class DEMSlopeFactor:
    """Windowed DEM slope factor producing raw slope values in degrees.

    The source must already be aligned to the requested ``SuitabilityGridSpec``. The factor reads
    deterministic row-major tiles with a one-cell halo, computes finite differences in metric grid
    units, and never requests the complete DEM merely to calculate local gradients.
    """

    def __init__(
        self,
        *,
        source: DEMSlopeSource,
        code: str = "slope",
        version: str = "v1",
        nodata_policy: DEMSlopeNoDataPolicy = DEMSlopeNoDataPolicy.INVALIDATE,
        vertical_scale_to_m: float = 1.0,
        tile_size: int = 256,
        max_cells: int = 25_000_000,
    ) -> None:
        _validate_factor_code(code)
        _validate_version(version)
        _validate_source(source)
        if not isinstance(nodata_policy, DEMSlopeNoDataPolicy):
            raise DEMSlopeError("nodata_policy must be a DEMSlopeNoDataPolicy value")
        scale = _require_positive_finite("vertical_scale_to_m", vertical_scale_to_m)
        _require_positive_int("tile_size", tile_size)
        _require_positive_int("max_cells", max_cells)
        if tile_size > 4096:
            raise DEMSlopeError("tile_size must be at most 4096")

        self.source = source
        self.code = code
        self.version = version
        self.nodata_policy = nodata_policy
        self.vertical_scale_to_m = scale
        self.tile_size = tile_size
        self.max_cells = max_cells

    def evaluate(
        self,
        *,
        grid: SuitabilityGridSpec,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> SuitabilityFactorResult:
        if not isinstance(grid, SuitabilityGridSpec):
            raise DEMSlopeError("grid must be a SuitabilityGridSpec")
        if self.source.grid != grid:
            raise DEMSlopeError("DEM source must use exactly the requested suitability grid")
        if snapshot.settings.working_srid != grid.working_srid:
            raise DEMSlopeError("snapshot working_srid must match the suitability grid")
        if context.working_srid != grid.working_srid:
            raise DEMSlopeError("run context working_srid must match the suitability grid")
        if grid.width < 2 or grid.height < 2:
            raise DEMSlopeError("DEM slope requires a grid at least 2 x 2 cells")
        if grid.cell_count > self.max_cells:
            raise DEMSlopeError(
                f"DEM slope grid cell limit exceeded: {grid.cell_count} > {self.max_cells}"
            )

        values = np.zeros(grid.shape, dtype=np.float64)
        valid_mask = np.zeros(grid.shape, dtype=np.bool_)
        windows_read = 0

        for row_off in range(0, grid.height, self.tile_size):
            core_height = min(self.tile_size, grid.height - row_off)
            for col_off in range(0, grid.width, self.tile_size):
                core_width = min(self.tile_size, grid.width - col_off)
                window = _halo_window(
                    row_off=row_off,
                    col_off=col_off,
                    height=core_height,
                    width=core_width,
                    grid=grid,
                )
                raw = self.source.read_window(window=window)
                elevations = _validated_window_array(window=window, values=raw)
                windows_read += 1

                tile_values, tile_valid = _calculate_tile_slope(
                    elevations=elevations,
                    window=window,
                    core_row_off=row_off,
                    core_col_off=col_off,
                    core_height=core_height,
                    core_width=core_width,
                    grid=grid,
                    nodata_policy=self.nodata_policy,
                    vertical_scale_to_m=self.vertical_scale_to_m,
                )
                row_slice = slice(row_off, row_off + core_height)
                col_slice = slice(col_off, col_off + core_width)
                values[row_slice, col_slice] = tile_values
                valid_mask[row_slice, col_slice] = tile_valid

        valid_count = int(np.count_nonzero(valid_mask))
        return SuitabilityFactorResult(
            code=self.code,
            version=self.version,
            grid=grid,
            values=values,
            valid_mask=valid_mask,
            diagnostics=(
                f"windows_read={windows_read}",
                f"valid_cells={valid_count}/{grid.cell_count}",
                f"nodata_policy={self.nodata_policy.value}",
                f"vertical_scale_to_m={self.vertical_scale_to_m:.12g}",
            ),
        )


def _halo_window(
    *,
    row_off: int,
    col_off: int,
    height: int,
    width: int,
    grid: SuitabilityGridSpec,
) -> DEMSlopeWindow:
    read_row_off = max(0, row_off - 1)
    read_col_off = max(0, col_off - 1)
    read_row_end = min(grid.height, row_off + height + 1)
    read_col_end = min(grid.width, col_off + width + 1)
    return DEMSlopeWindow(
        row_off=read_row_off,
        col_off=read_col_off,
        height=read_row_end - read_row_off,
        width=read_col_end - read_col_off,
    )


def _validated_window_array(
    *,
    window: DEMSlopeWindow,
    values: tuple[tuple[float | int | None, ...], ...],
) -> np.ndarray:
    if not isinstance(values, tuple) or len(values) != window.height:
        raise DEMSlopeError("DEM source returned an unexpected window height")

    array = np.full((window.height, window.width), np.nan, dtype=np.float64)
    for row_index, row in enumerate(values):
        if not isinstance(row, tuple) or len(row) != window.width:
            raise DEMSlopeError("DEM source returned an unexpected window width")
        for col_index, raw_value in enumerate(row):
            if raw_value is None:
                continue
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise DEMSlopeError("DEM elevations must be numeric or None")
            value = float(raw_value)
            if not math.isfinite(value):
                raise DEMSlopeError("DEM elevations must be finite or None")
            array[row_index, col_index] = value
    return array


def _calculate_tile_slope(
    *,
    elevations: np.ndarray,
    window: DEMSlopeWindow,
    core_row_off: int,
    core_col_off: int,
    core_height: int,
    core_width: int,
    grid: SuitabilityGridSpec,
    nodata_policy: DEMSlopeNoDataPolicy,
    vertical_scale_to_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    scaled = elevations * vertical_scale_to_m
    padded = np.pad(scaled, pad_width=1, mode="constant", constant_values=np.nan)

    local_row = core_row_off - window.row_off + 1
    local_col = core_col_off - window.col_off + 1
    row_end = local_row + core_height
    col_end = local_col + core_width

    center = padded[local_row:row_end, local_col:col_end]
    left = padded[local_row:row_end, local_col - 1 : col_end - 1]
    right = padded[local_row:row_end, local_col + 1 : col_end + 1]
    up = padded[local_row - 1 : row_end - 1, local_col:col_end]
    down = padded[local_row + 1 : row_end + 1, local_col:col_end]

    center_valid = np.isfinite(center)
    left_valid = np.isfinite(left)
    right_valid = np.isfinite(right)
    up_valid = np.isfinite(up)
    down_valid = np.isfinite(down)

    global_cols = np.arange(core_col_off, core_col_off + core_width)[None, :]
    global_rows = np.arange(core_row_off, core_row_off + core_height)[:, None]
    at_left_edge = np.broadcast_to(global_cols == 0, center.shape)
    at_right_edge = np.broadcast_to(global_cols == grid.width - 1, center.shape)
    at_top_edge = np.broadcast_to(global_rows == 0, center.shape)
    at_bottom_edge = np.broadcast_to(global_rows == grid.height - 1, center.shape)

    dx = np.zeros(center.shape, dtype=np.float64)
    dx_valid = np.zeros(center.shape, dtype=np.bool_)
    central_x = left_valid & right_valid
    dx[central_x] = (right[central_x] - left[central_x]) / (2.0 * grid.cell_width_m)
    dx_valid[central_x] = True

    forward_x = (~central_x) & center_valid & right_valid & at_left_edge
    backward_x = (~central_x) & center_valid & left_valid & at_right_edge
    if nodata_policy is DEMSlopeNoDataPolicy.ONE_SIDED:
        forward_x |= (~central_x) & center_valid & right_valid & ~left_valid
        backward_x |= (~central_x) & center_valid & left_valid & ~right_valid & ~forward_x
    dx[forward_x] = (right[forward_x] - center[forward_x]) / grid.cell_width_m
    dx[backward_x] = (center[backward_x] - left[backward_x]) / grid.cell_width_m
    dx_valid |= forward_x | backward_x

    dy = np.zeros(center.shape, dtype=np.float64)
    dy_valid = np.zeros(center.shape, dtype=np.bool_)
    central_y = up_valid & down_valid
    dy[central_y] = (down[central_y] - up[central_y]) / (2.0 * grid.cell_height_m)
    dy_valid[central_y] = True

    forward_y = (~central_y) & center_valid & down_valid & at_top_edge
    backward_y = (~central_y) & center_valid & up_valid & at_bottom_edge
    if nodata_policy is DEMSlopeNoDataPolicy.ONE_SIDED:
        forward_y |= (~central_y) & center_valid & down_valid & ~up_valid
        backward_y |= (~central_y) & center_valid & up_valid & ~down_valid & ~forward_y
    dy[forward_y] = (down[forward_y] - center[forward_y]) / grid.cell_height_m
    dy[backward_y] = (center[backward_y] - up[backward_y]) / grid.cell_height_m
    dy_valid |= forward_y | backward_y

    valid = center_valid & dx_valid & dy_valid
    slope = np.zeros(center.shape, dtype=np.float64)
    gradient = np.hypot(dx[valid], dy[valid])
    slope[valid] = np.degrees(np.arctan(gradient))
    return slope, valid


def _validate_source(source: DEMSlopeSource) -> None:
    try:
        grid = source.grid
        read_window = source.read_window
    except AttributeError as exc:
        raise DEMSlopeError("source must expose grid and read_window") from exc
    if not isinstance(grid, SuitabilityGridSpec):
        raise DEMSlopeError("source grid must be a SuitabilityGridSpec")
    if not callable(read_window):
        raise DEMSlopeError("source read_window must be callable")


def _require_positive_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DEMSlopeError(f"{field_name} must be a positive finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise DEMSlopeError(f"{field_name} must be a positive finite number")
    return number


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DEMSlopeError(f"{field_name} must be a positive integer")
