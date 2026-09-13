from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.suitability.factors import SuitabilityGridSpec


class HardExclusionMaskError(ValueError):
    """Raised when hard exclusion mask inputs violate the rasterization contract."""


class ExclusionRasterizationPolicy(StrEnum):
    """How vector exclusions are burned into the target grid."""

    CELL_CENTER = "CELL_CENTER"
    ANY_TOUCH = "ANY_TOUCH"


@dataclass(frozen=True, slots=True)
class HardExclusionBoundary:
    """Project boundary expressed in the same explicit metric CRS as the target grid."""

    geometry: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        _require_geometry("boundary", self.geometry)
        if self.geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise HardExclusionMaskError("boundary must be Polygon or MultiPolygon")


@dataclass(frozen=True, slots=True)
class HardExclusionGeometryLayer:
    """One named immutable vector source whose covered cells are forbidden."""

    code: str
    geometries: tuple[BaseGeometry, ...]
    working_srid: int

    def __post_init__(self) -> None:
        _validate_code(self.code)
        require_working_crs(self.working_srid)
        if not isinstance(self.geometries, tuple):
            raise HardExclusionMaskError("geometry layer geometries must be an immutable tuple")
        for index, geometry in enumerate(self.geometries):
            _require_geometry(f"geometries[{index}]", geometry)


@dataclass(frozen=True, slots=True)
class HardExclusionRasterLayer:
    """One named precomputed hard mask already aligned to the suitability grid."""

    code: str
    grid: SuitabilityGridSpec
    excluded: NDArray[np.bool_]

    def __post_init__(self) -> None:
        _validate_code(self.code)
        if not isinstance(self.grid, SuitabilityGridSpec):
            raise HardExclusionMaskError("raster layer grid must be a SuitabilityGridSpec")
        mask = np.asarray(self.excluded, dtype=np.bool_)
        if mask.ndim != 2 or mask.shape != self.grid.shape:
            raise HardExclusionMaskError("raster layer mask shape must match its grid")
        frozen = np.array(mask, dtype=np.bool_, copy=True)
        frozen.setflags(write=False)
        object.__setattr__(self, "excluded", frozen)


@dataclass(frozen=True, slots=True)
class HardExclusionMask:
    """Combined immutable hard exclusion mask kept separate from soft suitability scores."""

    grid: SuitabilityGridSpec
    excluded: NDArray[np.bool_]
    source_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.grid, SuitabilityGridSpec):
            raise HardExclusionMaskError("hard exclusion grid must be a SuitabilityGridSpec")
        mask = np.asarray(self.excluded, dtype=np.bool_)
        if mask.ndim != 2 or mask.shape != self.grid.shape:
            raise HardExclusionMaskError("hard exclusion mask shape must match the grid")
        frozen = np.array(mask, dtype=np.bool_, copy=True)
        frozen.setflags(write=False)
        object.__setattr__(self, "excluded", frozen)

        if not isinstance(self.source_codes, tuple):
            raise HardExclusionMaskError("source_codes must be an immutable tuple")
        for code in self.source_codes:
            _validate_code(code)
        if len(self.source_codes) != len(set(self.source_codes)):
            raise HardExclusionMaskError("hard exclusion source codes must be unique")

    @property
    def excluded_count(self) -> int:
        return int(np.count_nonzero(self.excluded))

    @property
    def developable_count(self) -> int:
        return self.grid.cell_count - self.excluded_count

    @property
    def excluded_fraction(self) -> float:
        return self.excluded_count / self.grid.cell_count


def build_hard_exclusion_mask(
    *,
    grid: SuitabilityGridSpec,
    boundary: HardExclusionBoundary,
    geometry_layers: tuple[HardExclusionGeometryLayer, ...] = (),
    raster_layers: tuple[HardExclusionRasterLayer, ...] = (),
    exclusion_policy: ExclusionRasterizationPolicy = ExclusionRasterizationPolicy.ANY_TOUCH,
    max_shapes: int = 100_000,
    max_cells: int = 25_000_000,
) -> HardExclusionMask:
    """Rasterize and combine hard constraints into one bounded deterministic mask.

    Boundary membership always uses cell-center semantics. Vector exclusion layers can use
    conservative ANY_TOUCH semantics or CELL_CENTER semantics. Precomputed raster layers are ORed
    after exact grid equality validation.
    """

    if not isinstance(grid, SuitabilityGridSpec):
        raise HardExclusionMaskError("grid must be a SuitabilityGridSpec")
    if not isinstance(boundary, HardExclusionBoundary):
        raise HardExclusionMaskError("boundary must be a HardExclusionBoundary")
    if boundary.working_srid != grid.working_srid:
        raise HardExclusionMaskError("boundary working_srid must match the target grid")
    if not isinstance(geometry_layers, tuple):
        raise HardExclusionMaskError("geometry_layers must be an immutable tuple")
    if not isinstance(raster_layers, tuple):
        raise HardExclusionMaskError("raster_layers must be an immutable tuple")
    if any(not isinstance(layer, HardExclusionGeometryLayer) for layer in geometry_layers):
        raise HardExclusionMaskError(
            "geometry_layers must contain only HardExclusionGeometryLayer values"
        )
    if any(not isinstance(layer, HardExclusionRasterLayer) for layer in raster_layers):
        raise HardExclusionMaskError(
            "raster_layers must contain only HardExclusionRasterLayer values"
        )
    if not isinstance(exclusion_policy, ExclusionRasterizationPolicy):
        raise HardExclusionMaskError(
            "exclusion_policy must be an ExclusionRasterizationPolicy value"
        )
    _require_positive_int("max_shapes", max_shapes)
    _require_positive_int("max_cells", max_cells)
    if grid.cell_count > max_cells:
        raise HardExclusionMaskError(
            f"hard exclusion grid cell limit exceeded: {grid.cell_count} > {max_cells}"
        )

    shape_count = sum(len(layer.geometries) for layer in geometry_layers)
    if shape_count > max_shapes:
        raise HardExclusionMaskError(
            f"hard exclusion shape limit exceeded: {shape_count} > {max_shapes}"
        )

    source_codes = (
        "boundary",
        *(layer.code for layer in geometry_layers),
        *(layer.code for layer in raster_layers),
    )
    if len(source_codes) != len(set(source_codes)):
        raise HardExclusionMaskError("hard exclusion source codes must be unique")

    for layer in geometry_layers:
        if layer.working_srid != grid.working_srid:
            raise HardExclusionMaskError(
                f"geometry layer {layer.code!r} working_srid must match the target grid"
            )
    for layer in raster_layers:
        if layer.grid != grid:
            raise HardExclusionMaskError(
                f"raster layer {layer.code!r} must use exactly the target grid"
            )

    transform = from_bounds(*grid.bounds, width=grid.width, height=grid.height)
    inside_boundary = rasterize(
        ((boundary.geometry, 1),),
        out_shape=grid.shape,
        transform=transform,
        fill=0,
        default_value=1,
        all_touched=False,
        dtype="uint8",
    ).astype(np.bool_)
    excluded = np.logical_not(inside_boundary)

    all_touched = exclusion_policy is ExclusionRasterizationPolicy.ANY_TOUCH
    for layer in geometry_layers:
        if not layer.geometries:
            continue
        burned = rasterize(
            ((geometry, 1) for geometry in layer.geometries),
            out_shape=grid.shape,
            transform=transform,
            fill=0,
            default_value=1,
            all_touched=all_touched,
            dtype="uint8",
        ).astype(np.bool_)
        np.logical_or(excluded, burned, out=excluded)

    for layer in raster_layers:
        np.logical_or(excluded, layer.excluded, out=excluded)

    return HardExclusionMask(
        grid=grid,
        excluded=excluded,
        source_codes=source_codes,
    )


def _validate_code(code: str) -> None:
    if not isinstance(code, str) or _CODE_RE.fullmatch(code) is None:
        raise HardExclusionMaskError(f"invalid hard exclusion source code: {code!r}")


def _require_geometry(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, BaseGeometry):
        raise HardExclusionMaskError(f"{field_name} must be a Shapely geometry")
    if geometry.is_empty:
        raise HardExclusionMaskError(f"{field_name} geometry must not be empty")
    if not geometry.is_valid:
        raise HardExclusionMaskError(f"{field_name} geometry must be valid")
    bounds = geometry.bounds
    if len(bounds) != 4 or any(not math.isfinite(float(value)) for value in bounds):
        raise HardExclusionMaskError(f"{field_name} geometry must have finite bounds")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HardExclusionMaskError(f"{field_name} must be a positive integer")


_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
