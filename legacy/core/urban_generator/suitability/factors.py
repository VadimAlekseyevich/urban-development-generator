from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot
from core.urban_generator.suitability.config import SuitabilityConfigError


class SuitabilityFactorError(SuitabilityConfigError):
    """Raised when a factor grid/result violates the suitability factor contract."""


@dataclass(frozen=True, slots=True)
class SuitabilityGridSpec:
    """Target raster grid shared by all factors in one suitability evaluation."""

    working_srid: int
    bounds: tuple[float, float, float, float]
    width: int
    height: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.bounds, tuple) or len(self.bounds) != 4:
            raise SuitabilityFactorError("grid bounds must be an immutable 4-value tuple")
        min_x, min_y, max_x, max_y = (
            _require_finite_number(f"bounds[{index}]", value)
            for index, value in enumerate(self.bounds)
        )
        if max_x <= min_x or max_y <= min_y:
            raise SuitabilityFactorError("grid bounds must have positive width and height")
        object.__setattr__(self, "bounds", (min_x, min_y, max_x, max_y))

        _require_positive_int("width", self.width)
        _require_positive_int("height", self.height)

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def cell_width_m(self) -> float:
        min_x, _min_y, max_x, _max_y = self.bounds
        return (max_x - min_x) / self.width

    @property
    def cell_height_m(self) -> float:
        _min_x, min_y, _max_x, max_y = self.bounds
        return (max_y - min_y) / self.height

    @property
    def cell_count(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class SuitabilityFactorResult:
    """Immutable raw factor raster plus an explicit validity mask."""

    code: str
    version: str
    grid: SuitabilityGridSpec
    values: NDArray[np.float64]
    valid_mask: NDArray[np.bool_]
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        from core.urban_generator.suitability.config import _validate_factor_code, _validate_version

        _validate_factor_code(self.code)
        _validate_version(self.version)
        if not isinstance(self.grid, SuitabilityGridSpec):
            raise SuitabilityFactorError("factor result grid must be a SuitabilityGridSpec")

        values = np.asarray(self.values, dtype=np.float64)
        valid_mask = np.asarray(self.valid_mask, dtype=np.bool_)
        if values.ndim != 2:
            raise SuitabilityFactorError("factor values must be a 2D array")
        if valid_mask.ndim != 2:
            raise SuitabilityFactorError("factor valid_mask must be a 2D array")
        if values.shape != self.grid.shape or valid_mask.shape != self.grid.shape:
            raise SuitabilityFactorError(
                "factor values and valid_mask shapes must match the target grid"
            )
        if np.any(~np.isfinite(values[valid_mask])):
            raise SuitabilityFactorError("valid factor cells must contain finite values")

        frozen_values = np.array(values, dtype=np.float64, copy=True)
        frozen_mask = np.array(valid_mask, dtype=np.bool_, copy=True)
        frozen_values.setflags(write=False)
        frozen_mask.setflags(write=False)
        object.__setattr__(self, "values", frozen_values)
        object.__setattr__(self, "valid_mask", frozen_mask)

        if not isinstance(self.diagnostics, tuple):
            raise SuitabilityFactorError("factor diagnostics must be an immutable tuple")
        if any(not isinstance(item, str) or not item.strip() for item in self.diagnostics):
            raise SuitabilityFactorError(
                "factor diagnostics must contain only non-empty strings"
            )


@runtime_checkable
class SuitabilityFactor(Protocol):
    """Infrastructure-independent factor contract for future suitability implementations."""

    code: str
    version: str

    def evaluate(
        self,
        *,
        grid: SuitabilityGridSpec,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> SuitabilityFactorResult:
        """Evaluate raw values for exactly the requested target grid."""


def validate_factor_result(
    *,
    factor: SuitabilityFactor,
    result: SuitabilityFactorResult,
    grid: SuitabilityGridSpec,
) -> None:
    """Enforce factor identity and grid consistency at orchestration boundaries."""

    if not isinstance(result, SuitabilityFactorResult):
        raise SuitabilityFactorError("suitability factor must return SuitabilityFactorResult")
    if result.code != factor.code:
        raise SuitabilityFactorError(
            f"factor {factor.code!r} returned mismatched code {result.code!r}"
        )
    if result.version != factor.version:
        raise SuitabilityFactorError(
            f"factor {factor.code!r} returned mismatched version {result.version!r}"
        )
    if result.grid != grid:
        raise SuitabilityFactorError(
            f"factor {factor.code!r} returned a result for a different grid"
        )


def _require_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SuitabilityFactorError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise SuitabilityFactorError(f"{field_name} must be a finite number")
    return number


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SuitabilityFactorError(f"{field_name} must be a positive integer")
