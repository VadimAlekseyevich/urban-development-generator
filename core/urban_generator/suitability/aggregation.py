from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from core.urban_generator.suitability.config import (
    SuitabilityConfig,
    SuitabilityFactorConfig,
    SuitabilityNormalization,
)
from core.urban_generator.suitability.factors import (
    SuitabilityFactorError,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
)
from core.urban_generator.suitability.hard_exclusion import HardExclusionMask


class WeightedSuitabilityError(SuitabilityFactorError):
    """Raised when weighted suitability inputs violate the aggregation contract."""


@dataclass(frozen=True, slots=True)
class WeightedSuitabilityResult:
    """Immutable final soft score with hard exclusions and data validity kept explicit."""

    grid: SuitabilityGridSpec
    scores: NDArray[np.float64]
    valid_mask: NDArray[np.bool_]
    hard_excluded_mask: NDArray[np.bool_]
    config_version: str
    config_fingerprint: str
    factor_versions: tuple[tuple[str, str], ...]
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.grid, SuitabilityGridSpec):
            raise WeightedSuitabilityError("weighted result grid must be a SuitabilityGridSpec")

        scores = np.asarray(self.scores, dtype=np.float64)
        valid_mask = np.asarray(self.valid_mask, dtype=np.bool_)
        hard_mask = np.asarray(self.hard_excluded_mask, dtype=np.bool_)
        if scores.ndim != 2 or valid_mask.ndim != 2 or hard_mask.ndim != 2:
            raise WeightedSuitabilityError("weighted result arrays must be 2D")
        if (
            scores.shape != self.grid.shape
            or valid_mask.shape != self.grid.shape
            or hard_mask.shape != self.grid.shape
        ):
            raise WeightedSuitabilityError("weighted result arrays must match the target grid")
        if np.any(~np.isfinite(scores)):
            raise WeightedSuitabilityError("weighted scores must be finite")
        if np.any(scores < 0.0) or np.any(scores > 1.0):
            raise WeightedSuitabilityError("weighted scores must stay inside 0..1")
        if np.any(valid_mask & hard_mask):
            raise WeightedSuitabilityError("hard-excluded cells must never be valid")
        if np.any(scores[~valid_mask] != 0.0):
            raise WeightedSuitabilityError("invalid or hard-excluded cells must have score 0")

        frozen_scores = np.array(scores, dtype=np.float64, copy=True)
        frozen_valid = np.array(valid_mask, dtype=np.bool_, copy=True)
        frozen_hard = np.array(hard_mask, dtype=np.bool_, copy=True)
        frozen_scores.setflags(write=False)
        frozen_valid.setflags(write=False)
        frozen_hard.setflags(write=False)
        object.__setattr__(self, "scores", frozen_scores)
        object.__setattr__(self, "valid_mask", frozen_valid)
        object.__setattr__(self, "hard_excluded_mask", frozen_hard)

        if not isinstance(self.config_version, str) or not self.config_version:
            raise WeightedSuitabilityError("config_version must be a non-empty string")
        if (
            not isinstance(self.config_fingerprint, str)
            or _SHA256_RE.fullmatch(self.config_fingerprint) is None
        ):
            raise WeightedSuitabilityError(
                "config_fingerprint must be a lowercase SHA-256 hex digest"
            )
        if not isinstance(self.factor_versions, tuple) or not self.factor_versions:
            raise WeightedSuitabilityError("factor_versions must be a non-empty immutable tuple")
        factor_codes: list[str] = []
        for item in self.factor_versions:
            if not isinstance(item, tuple) or len(item) != 2:
                raise WeightedSuitabilityError(
                    "factor_versions entries must be (code, version) tuples"
                )
            code, version = item
            if not isinstance(code, str) or not code:
                raise WeightedSuitabilityError("factor version code must be a non-empty string")
            if not isinstance(version, str) or not version:
                raise WeightedSuitabilityError("factor version must be a non-empty string")
            factor_codes.append(code)
        if len(factor_codes) != len(set(factor_codes)):
            raise WeightedSuitabilityError("factor version codes must be unique")

        if not isinstance(self.diagnostics, tuple):
            raise WeightedSuitabilityError("weighted diagnostics must be an immutable tuple")
        if any(not isinstance(item, str) or not item.strip() for item in self.diagnostics):
            raise WeightedSuitabilityError(
                "weighted diagnostics must contain only non-empty strings"
            )

    @property
    def valid_count(self) -> int:
        return int(np.count_nonzero(self.valid_mask))

    @property
    def hard_excluded_count(self) -> int:
        return int(np.count_nonzero(self.hard_excluded_mask))

    @property
    def invalid_data_count(self) -> int:
        return int(np.count_nonzero(~self.valid_mask & ~self.hard_excluded_mask))


def aggregate_weighted_suitability(
    *,
    grid: SuitabilityGridSpec,
    config: SuitabilityConfig,
    hard_mask: HardExclusionMask,
    factor_results: tuple[SuitabilityFactorResult, ...],
    max_cells: int = 25_000_000,
) -> WeightedSuitabilityResult:
    """Normalize configured factors and combine them into one deterministic score raster.

    Every positive-weight factor must be present. A non-hard-excluded cell is valid only when all
    positive-weight factor results are valid there. Missing data never causes local weight
    renormalization. Hard exclusions always win and force ``score=0`` and ``valid=False``.
    """

    if not isinstance(grid, SuitabilityGridSpec):
        raise WeightedSuitabilityError("grid must be a SuitabilityGridSpec")
    if not isinstance(config, SuitabilityConfig):
        raise WeightedSuitabilityError("config must be a SuitabilityConfig")
    if not isinstance(hard_mask, HardExclusionMask):
        raise WeightedSuitabilityError("hard_mask must be a HardExclusionMask")
    if hard_mask.grid != grid:
        raise WeightedSuitabilityError("hard mask must use exactly the target grid")
    if not isinstance(factor_results, tuple):
        raise WeightedSuitabilityError("factor_results must be an immutable tuple")
    if any(not isinstance(result, SuitabilityFactorResult) for result in factor_results):
        raise WeightedSuitabilityError(
            "factor_results must contain only SuitabilityFactorResult values"
        )
    _require_positive_int("max_cells", max_cells)
    if grid.cell_count > max_cells:
        raise WeightedSuitabilityError(
            f"weighted suitability grid cell limit exceeded: {grid.cell_count} > {max_cells}"
        )

    configured_codes = {factor.code for factor in config.factors}
    results_by_code: dict[str, SuitabilityFactorResult] = {}
    for result in factor_results:
        if result.code not in configured_codes:
            raise WeightedSuitabilityError(
                f"factor result {result.code!r} is not present in the suitability config"
            )
        if result.code in results_by_code:
            raise WeightedSuitabilityError(f"duplicate factor result for {result.code!r}")
        if result.grid != grid:
            raise WeightedSuitabilityError(
                f"factor result {result.code!r} must use exactly the target grid"
            )
        results_by_code[result.code] = result

    positive_factors = tuple(factor for factor in config.factors if factor.weight > 0.0)
    missing_codes = tuple(
        factor.code for factor in positive_factors if factor.code not in results_by_code
    )
    if missing_codes:
        raise WeightedSuitabilityError(
            "missing positive-weight factor results: " + ", ".join(missing_codes)
        )

    scores = np.zeros(grid.shape, dtype=np.float64)
    valid_mask = np.logical_not(hard_mask.excluded).copy()
    factor_versions: list[tuple[str, str]] = []

    for factor_config in positive_factors:
        result = results_by_code[factor_config.code]
        normalized = _normalize_factor_values(config=factor_config, result=result)
        normalized_weight = factor_config.weight / config.total_weight
        scores += normalized * normalized_weight
        np.logical_and(valid_mask, result.valid_mask, out=valid_mask)
        factor_versions.append((result.code, result.version))

    np.clip(scores, 0.0, 1.0, out=scores)
    scores[~valid_mask] = 0.0

    valid_count = int(np.count_nonzero(valid_mask))
    hard_count = int(np.count_nonzero(hard_mask.excluded))
    invalid_data_count = int(np.count_nonzero(~valid_mask & ~hard_mask.excluded))
    return WeightedSuitabilityResult(
        grid=grid,
        scores=scores,
        valid_mask=valid_mask,
        hard_excluded_mask=hard_mask.excluded,
        config_version=config.version,
        config_fingerprint=config.fingerprint,
        factor_versions=tuple(factor_versions),
        diagnostics=(
            f"positive_weight_factors={len(positive_factors)}",
            f"valid_cells={valid_count}/{grid.cell_count}",
            f"hard_excluded_cells={hard_count}",
            f"invalid_data_cells={invalid_data_count}",
            "invalid_data_policy=REQUIRE_ALL_POSITIVE_WEIGHT_FACTORS",
            "hard_mask_precedence=FORCE_ZERO_AND_INVALID",
        ),
    )


def _normalize_factor_values(
    *,
    config: SuitabilityFactorConfig,
    result: SuitabilityFactorResult,
) -> NDArray[np.float64]:
    normalized = np.zeros(result.grid.shape, dtype=np.float64)
    valid_values = result.values[result.valid_mask]
    if valid_values.size == 0:
        return normalized

    if config.normalization is SuitabilityNormalization.IDENTITY:
        if np.any(valid_values < 0.0) or np.any(valid_values > 1.0):
            raise WeightedSuitabilityError(
                f"IDENTITY factor {config.code!r} contains valid values outside 0..1"
            )
        normalized[result.valid_mask] = valid_values
        return normalized

    if config.raw_min is None or config.raw_max is None:
        raise WeightedSuitabilityError(
            f"factor {config.code!r} normalization bounds are unexpectedly missing"
        )
    scaled = (valid_values - config.raw_min) / (config.raw_max - config.raw_min)
    clipped = np.clip(scaled, 0.0, 1.0)
    if config.normalization is SuitabilityNormalization.INVERTED_MIN_MAX:
        clipped = 1.0 - clipped
    normalized[result.valid_mask] = clipped
    return normalized


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WeightedSuitabilityError(f"{field_name} must be a positive integer")


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
