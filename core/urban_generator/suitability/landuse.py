from __future__ import annotations

import hashlib
import json
import math
import re
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


class LanduseFactorError(SuitabilityFactorError):
    """Raised when landuse suitability inputs violate the categorical factor contract."""


class LanduseUnknownClassPolicy(StrEnum):
    """How source classes absent from the configured mapping are handled."""

    INVALIDATE = "INVALIDATE"
    USE_DEFAULT = "USE_DEFAULT"


@dataclass(frozen=True, slots=True)
class LanduseClassWeight:
    """One exact source-class to normalized suitability-score mapping."""

    class_name: str
    score: float

    def __post_init__(self) -> None:
        _validate_class_name(self.class_name)
        object.__setattr__(self, "score", _require_unit_interval("score", self.score))


@dataclass(frozen=True, slots=True)
class LanduseClassWeights:
    """Immutable versioned class mapping used by one landuse factor run."""

    version: str
    classes: tuple[LanduseClassWeight, ...]
    unknown_policy: LanduseUnknownClassPolicy = LanduseUnknownClassPolicy.INVALIDATE
    default_score: float | None = None

    def __post_init__(self) -> None:
        try:
            _validate_version(self.version)
        except ValueError as exc:
            raise LanduseFactorError(f"invalid landuse weights version: {self.version!r}") from exc
        if not isinstance(self.classes, tuple):
            raise LanduseFactorError("landuse classes must be an immutable tuple")
        if not self.classes:
            raise LanduseFactorError("landuse class weights must contain at least one class")
        if any(not isinstance(item, LanduseClassWeight) for item in self.classes):
            raise LanduseFactorError(
                "landuse classes must contain only LanduseClassWeight values"
            )
        class_names = tuple(item.class_name for item in self.classes)
        if len(class_names) != len(set(class_names)):
            raise LanduseFactorError("landuse class names must be unique")
        if not isinstance(self.unknown_policy, LanduseUnknownClassPolicy):
            raise LanduseFactorError(
                "unknown_policy must be a LanduseUnknownClassPolicy value"
            )
        if self.unknown_policy is LanduseUnknownClassPolicy.USE_DEFAULT:
            if self.default_score is None:
                raise LanduseFactorError("USE_DEFAULT requires default_score")
            object.__setattr__(
                self,
                "default_score",
                _require_unit_interval("default_score", self.default_score),
            )
        elif self.default_score is not None:
            raise LanduseFactorError("INVALIDATE must not define default_score")

    def score_for(self, class_name: str) -> float | None:
        """Return the configured normalized score or the explicit unknown fallback."""

        _validate_class_name(class_name)
        for item in self.classes:
            if item.class_name == class_name:
                return item.score
        if self.unknown_policy is LanduseUnknownClassPolicy.USE_DEFAULT:
            assert self.default_score is not None
            return self.default_score
        return None

    @property
    def fingerprint(self) -> str:
        payload = {
            "version": self.version,
            "classes": [
                {"class_name": item.class_name, "score": item.score}
                for item in self.classes
            ],
            "unknown_policy": self.unknown_policy.value,
            "default_score": self.default_score,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LanduseWindow:
    """One bounded categorical source window in target-grid pixel coordinates."""

    row_off: int
    col_off: int
    height: int
    width: int

    def __post_init__(self) -> None:
        for field_name in ("row_off", "col_off", "height", "width"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise LanduseFactorError(f"{field_name} must be an integer")
        if self.row_off < 0 or self.col_off < 0:
            raise LanduseFactorError("landuse window offsets must be non-negative")
        if self.height <= 0 or self.width <= 0:
            raise LanduseFactorError("landuse window dimensions must be positive")

    @property
    def cell_count(self) -> int:
        return self.height * self.width


class LanduseSource(Protocol):
    """Minimal aligned categorical source required by the core landuse factor."""

    grid: SuitabilityGridSpec

    def read_window(
        self,
        *,
        window: LanduseWindow,
    ) -> tuple[tuple[str | None, ...], ...]:
        """Return exactly ``height x width`` canonical classes; nodata is ``None``."""


class LanduseFactor:
    """Windowed configurable categorical suitability factor returning scores in 0..1."""

    def __init__(
        self,
        *,
        source: LanduseSource,
        weights: LanduseClassWeights,
        code: str = "landuse",
        version: str = "v1",
        tile_size: int = 256,
        max_cells: int = 25_000_000,
    ) -> None:
        try:
            _validate_factor_code(code)
            _validate_version(version)
        except ValueError as exc:
            raise LanduseFactorError("invalid landuse factor code or version") from exc
        _validate_source(source)
        if not isinstance(weights, LanduseClassWeights):
            raise LanduseFactorError("weights must be LanduseClassWeights")
        _require_positive_int("tile_size", tile_size)
        _require_positive_int("max_cells", max_cells)
        if tile_size > 4096:
            raise LanduseFactorError("tile_size must be at most 4096")

        self.source = source
        self.weights = weights
        self.code = code
        self.version = version
        self.tile_size = tile_size
        self.max_cells = max_cells
        self._scores = {item.class_name: item.score for item in weights.classes}

    def evaluate(
        self,
        *,
        grid: SuitabilityGridSpec,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> SuitabilityFactorResult:
        if not isinstance(grid, SuitabilityGridSpec):
            raise LanduseFactorError("grid must be a SuitabilityGridSpec")
        if self.source.grid != grid:
            raise LanduseFactorError("landuse source must use exactly the requested suitability grid")
        if snapshot.settings.working_srid != grid.working_srid:
            raise LanduseFactorError("snapshot working_srid must match the suitability grid")
        if context.working_srid != grid.working_srid:
            raise LanduseFactorError("run context working_srid must match the suitability grid")
        if grid.cell_count > self.max_cells:
            raise LanduseFactorError(
                f"landuse grid cell limit exceeded: {grid.cell_count} > {self.max_cells}"
            )

        values = np.zeros(grid.shape, dtype=np.float64)
        valid_mask = np.zeros(grid.shape, dtype=np.bool_)
        windows_read = 0
        nodata_cells = 0
        unknown_cells = 0
        defaulted_cells = 0

        for row_off in range(0, grid.height, self.tile_size):
            height = min(self.tile_size, grid.height - row_off)
            for col_off in range(0, grid.width, self.tile_size):
                width = min(self.tile_size, grid.width - col_off)
                window = LanduseWindow(
                    row_off=row_off,
                    col_off=col_off,
                    height=height,
                    width=width,
                )
                raw = self.source.read_window(window=window)
                _validate_window_values(window=window, values=raw)
                windows_read += 1

                tile_values = np.zeros((height, width), dtype=np.float64)
                tile_valid = np.zeros((height, width), dtype=np.bool_)
                for local_row, row in enumerate(raw):
                    for local_col, class_name in enumerate(row):
                        if class_name is None:
                            nodata_cells += 1
                            continue
                        score = self._scores.get(class_name)
                        if score is None:
                            unknown_cells += 1
                            if self.weights.unknown_policy is LanduseUnknownClassPolicy.INVALIDATE:
                                continue
                            assert self.weights.default_score is not None
                            score = self.weights.default_score
                            defaulted_cells += 1
                        tile_values[local_row, local_col] = score
                        tile_valid[local_row, local_col] = True

                row_slice = slice(row_off, row_off + height)
                col_slice = slice(col_off, col_off + width)
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
                f"weights_version={self.weights.version}",
                f"weights_fingerprint={self.weights.fingerprint}",
                f"windows_read={windows_read}",
                f"valid_cells={valid_count}/{grid.cell_count}",
                f"nodata_cells={nodata_cells}",
                f"unknown_cells={unknown_cells}",
                f"defaulted_cells={defaulted_cells}",
            ),
        )


def _validate_source(source: LanduseSource) -> None:
    try:
        grid = source.grid
        read_window = source.read_window
    except AttributeError as exc:
        raise LanduseFactorError("source must expose grid and read_window") from exc
    if not isinstance(grid, SuitabilityGridSpec):
        raise LanduseFactorError("source grid must be a SuitabilityGridSpec")
    if not callable(read_window):
        raise LanduseFactorError("source read_window must be callable")


def _validate_window_values(
    *,
    window: LanduseWindow,
    values: tuple[tuple[str | None, ...], ...],
) -> None:
    if not isinstance(values, tuple) or len(values) != window.height:
        raise LanduseFactorError("landuse source returned an unexpected window height")
    for row in values:
        if not isinstance(row, tuple) or len(row) != window.width:
            raise LanduseFactorError("landuse source returned an unexpected window width")
        for class_name in row:
            if class_name is None:
                continue
            _validate_class_name(class_name)


def _validate_class_name(class_name: str) -> None:
    if not isinstance(class_name, str):
        raise LanduseFactorError("landuse class name must be a string")
    if not class_name or class_name != class_name.strip() or len(class_name) > 128:
        raise LanduseFactorError(
            "landuse class name must be non-empty, trimmed, and at most 128 characters"
        )
    if _CONTROL_RE.search(class_name) is not None:
        raise LanduseFactorError("landuse class name must not contain control characters")


def _require_unit_interval(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LanduseFactorError(f"{field_name} must be a finite number inside 0..1")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise LanduseFactorError(f"{field_name} must be a finite number inside 0..1")
    return number


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LanduseFactorError(f"{field_name} must be a positive integer")


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
