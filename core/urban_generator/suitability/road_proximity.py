from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from shapely import points
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot
from core.urban_generator.suitability.config import _validate_factor_code, _validate_version
from core.urban_generator.suitability.factors import (
    SuitabilityFactorError,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
)


class RoadProximityError(SuitabilityFactorError):
    """Raised when road-proximity inputs violate the indexed distance contract."""


@dataclass(frozen=True, slots=True)
class RoadProximityTile:
    """One bounded target-grid tile in row/column coordinates."""

    row_off: int
    col_off: int
    height: int
    width: int

    def __post_init__(self) -> None:
        for field_name in ("row_off", "col_off", "height", "width"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise RoadProximityError(f"{field_name} must be an integer")
        if self.row_off < 0 or self.col_off < 0:
            raise RoadProximityError("road proximity tile offsets must be non-negative")
        if self.height <= 0 or self.width <= 0:
            raise RoadProximityError("road proximity tile dimensions must be positive")

    @property
    def cell_count(self) -> int:
        return self.height * self.width


class RoadProximityIndex:
    """Reusable STRtree-backed nearest-road index in one explicit metric CRS."""

    def __init__(
        self,
        *,
        roads: tuple[BaseGeometry, ...],
        working_srid: int,
        max_roads: int = 500_000,
    ) -> None:
        require_working_crs(working_srid)
        if not isinstance(roads, tuple):
            raise RoadProximityError("roads must be an immutable tuple")
        _require_positive_int("max_roads", max_roads)
        if len(roads) > max_roads:
            raise RoadProximityError(
                f"road proximity feature limit exceeded: {len(roads)} > {max_roads}"
            )
        for index, geometry in enumerate(roads):
            _require_road_geometry(f"roads[{index}]", geometry)

        self.working_srid = working_srid
        self.roads = roads
        self.max_roads = max_roads
        self._tree = STRtree(roads) if roads else None

    @property
    def feature_count(self) -> int:
        return len(self.roads)

    def nearest_distances(
        self,
        *,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
    ) -> NDArray[np.float64] | None:
        """Return exact Cartesian nearest-road distances for aligned point arrays.

        ``None`` means the index contains no roads. The query uses STRtree nearest-neighbour
        search and never constructs a point-by-road distance matrix.
        """

        x_values = np.asarray(x, dtype=np.float64)
        y_values = np.asarray(y, dtype=np.float64)
        if x_values.ndim != 1 or y_values.ndim != 1:
            raise RoadProximityError("road proximity query coordinates must be 1D arrays")
        if x_values.shape != y_values.shape:
            raise RoadProximityError("road proximity query coordinate shapes must match")
        if x_values.size == 0:
            raise RoadProximityError("road proximity query must contain at least one point")
        if np.any(~np.isfinite(x_values)) or np.any(~np.isfinite(y_values)):
            raise RoadProximityError("road proximity query coordinates must be finite")
        if self._tree is None:
            return None

        query_points = points(x_values, y_values)
        pair_indexes, raw_distances = self._tree.query_nearest(
            query_points,
            all_matches=False,
            return_distance=True,
        )
        indexes = np.asarray(pair_indexes)
        distances = np.asarray(raw_distances, dtype=np.float64)
        if indexes.ndim != 2 or indexes.shape[0] != 2:
            raise RoadProximityError("STRtree returned an unexpected nearest-index shape")
        if distances.ndim != 1 or distances.shape[0] != indexes.shape[1]:
            raise RoadProximityError("STRtree returned an unexpected nearest-distance shape")
        if indexes.shape[1] != x_values.size:
            raise RoadProximityError("STRtree did not return exactly one nearest road per point")

        input_indexes = np.asarray(indexes[0], dtype=np.int64)
        if (
            np.any(input_indexes < 0)
            or np.any(input_indexes >= x_values.size)
            or np.unique(input_indexes).size != x_values.size
        ):
            raise RoadProximityError("STRtree nearest results do not cover each query point once")
        if np.any(~np.isfinite(distances)) or np.any(distances < 0.0):
            raise RoadProximityError("STRtree returned invalid nearest-road distances")

        ordered = np.empty(x_values.size, dtype=np.float64)
        ordered[input_indexes] = distances
        return ordered


class RoadProximityFactor:
    """Indexed nearest-road distance factor producing raw values in metres."""

    def __init__(
        self,
        *,
        index: RoadProximityIndex,
        code: str = "road_proximity",
        version: str = "v1",
        tile_size: int = 256,
        max_cells: int = 25_000_000,
    ) -> None:
        _validate_factor_code(code)
        _validate_version(version)
        if not isinstance(index, RoadProximityIndex):
            raise RoadProximityError("index must be a RoadProximityIndex")
        _require_positive_int("tile_size", tile_size)
        _require_positive_int("max_cells", max_cells)
        if tile_size > 512:
            raise RoadProximityError("tile_size must be at most 512")

        self.index = index
        self.code = code
        self.version = version
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
            raise RoadProximityError("grid must be a SuitabilityGridSpec")
        if self.index.working_srid != grid.working_srid:
            raise RoadProximityError("road index working_srid must match the suitability grid")
        if snapshot.settings.working_srid != grid.working_srid:
            raise RoadProximityError("snapshot working_srid must match the suitability grid")
        if context.working_srid != grid.working_srid:
            raise RoadProximityError("run context working_srid must match the suitability grid")
        if grid.cell_count > self.max_cells:
            raise RoadProximityError(
                f"road proximity grid cell limit exceeded: {grid.cell_count} > {self.max_cells}"
            )

        values = np.zeros(grid.shape, dtype=np.float64)
        valid_mask = np.zeros(grid.shape, dtype=np.bool_)
        tiles_queried = 0
        max_query_points = 0

        if self.index.feature_count == 0:
            return SuitabilityFactorResult(
                code=self.code,
                version=self.version,
                grid=grid,
                values=values,
                valid_mask=valid_mask,
                diagnostics=(
                    "strategy=STRtree.query_nearest",
                    "roads=0",
                    "valid_cells=0",
                ),
            )

        for tile in _tiles(grid=grid, tile_size=self.tile_size):
            x, y = _tile_cell_centers(grid=grid, tile=tile)
            distances = self.index.nearest_distances(x=x, y=y)
            if distances is None:
                raise RoadProximityError("road index became empty during factor evaluation")
            if distances.shape != (tile.cell_count,):
                raise RoadProximityError("road index returned an unexpected distance count")

            row_slice = slice(tile.row_off, tile.row_off + tile.height)
            col_slice = slice(tile.col_off, tile.col_off + tile.width)
            values[row_slice, col_slice] = distances.reshape(tile.height, tile.width)
            valid_mask[row_slice, col_slice] = True
            tiles_queried += 1
            max_query_points = max(max_query_points, tile.cell_count)

        return SuitabilityFactorResult(
            code=self.code,
            version=self.version,
            grid=grid,
            values=values,
            valid_mask=valid_mask,
            diagnostics=(
                "strategy=STRtree.query_nearest",
                f"roads={self.index.feature_count}",
                f"tiles_queried={tiles_queried}",
                f"max_query_points={max_query_points}",
                f"valid_cells={grid.cell_count}",
            ),
        )


def _tiles(*, grid: SuitabilityGridSpec, tile_size: int) -> tuple[RoadProximityTile, ...]:
    tiles: list[RoadProximityTile] = []
    for row_off in range(0, grid.height, tile_size):
        height = min(tile_size, grid.height - row_off)
        for col_off in range(0, grid.width, tile_size):
            width = min(tile_size, grid.width - col_off)
            tiles.append(
                RoadProximityTile(
                    row_off=row_off,
                    col_off=col_off,
                    height=height,
                    width=width,
                )
            )
    return tuple(tiles)


def _tile_cell_centers(
    *,
    grid: SuitabilityGridSpec,
    tile: RoadProximityTile,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    min_x, _min_y, _max_x, max_y = grid.bounds
    columns = np.arange(tile.col_off, tile.col_off + tile.width, dtype=np.float64)
    rows = np.arange(tile.row_off, tile.row_off + tile.height, dtype=np.float64)
    x_axis = min_x + (columns + 0.5) * grid.cell_width_m
    y_axis = max_y - (rows + 0.5) * grid.cell_height_m
    x_grid, y_grid = np.meshgrid(x_axis, y_axis)
    return x_grid.ravel(), y_grid.ravel()


def _require_road_geometry(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, BaseGeometry):
        raise RoadProximityError(f"{field_name} must be a Shapely geometry")
    if geometry.geom_type not in {"LineString", "MultiLineString"}:
        raise RoadProximityError(f"{field_name} must be LineString or MultiLineString")
    if geometry.is_empty:
        raise RoadProximityError(f"{field_name} geometry must not be empty")
    if not geometry.is_valid:
        raise RoadProximityError(f"{field_name} geometry must be valid")
    bounds = geometry.bounds
    if len(bounds) != 4 or any(not math.isfinite(float(value)) for value in bounds):
        raise RoadProximityError(f"{field_name} geometry must have finite bounds")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RoadProximityError(f"{field_name} must be a positive integer")
