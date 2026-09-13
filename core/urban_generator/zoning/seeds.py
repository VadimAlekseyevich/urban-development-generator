from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.suitability.aggregation import WeightedSuitabilityResult


class ZoningSeedError(ValueError):
    """Raised when deterministic zoning seed generation violates its contract."""


@dataclass(frozen=True, slots=True)
class ZoningSeed:
    """One deterministic seed at the center of a valid suitability raster cell."""

    row: int
    col: int
    x_m: float
    y_m: float
    suitability_score: float

    def __post_init__(self) -> None:
        if isinstance(self.row, bool) or not isinstance(self.row, int) or self.row < 0:
            raise ZoningSeedError("seed row must be a non-negative integer")
        if isinstance(self.col, bool) or not isinstance(self.col, int) or self.col < 0:
            raise ZoningSeedError("seed col must be a non-negative integer")
        if not math.isfinite(self.x_m) or not math.isfinite(self.y_m):
            raise ZoningSeedError("seed coordinates must be finite")
        if not math.isfinite(self.suitability_score):
            raise ZoningSeedError("seed suitability_score must be finite")
        if self.suitability_score < 0.0 or self.suitability_score > 1.0:
            raise ZoningSeedError("seed suitability_score must stay inside 0..1")


@dataclass(frozen=True, slots=True)
class ZoningSeedSet:
    """Deterministic seed output plus RNG provenance and fallback diagnostics."""

    seeds: tuple[ZoningSeed, ...]
    rng_namespace: str
    rng_seed: int
    weighted_seed_count: int
    uniform_fallback_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.seeds, tuple) or any(
            not isinstance(seed, ZoningSeed) for seed in self.seeds
        ):
            raise ZoningSeedError("seeds must be an immutable tuple of ZoningSeed values")
        if not isinstance(self.rng_namespace, str) or not self.rng_namespace.strip():
            raise ZoningSeedError("rng_namespace must be a non-empty string")
        if isinstance(self.rng_seed, bool) or not isinstance(self.rng_seed, int) or self.rng_seed < 0:
            raise ZoningSeedError("rng_seed must be a non-negative integer")
        _require_non_negative_int("weighted_seed_count", self.weighted_seed_count)
        _require_non_negative_int("uniform_fallback_count", self.uniform_fallback_count)
        if self.weighted_seed_count + self.uniform_fallback_count != len(self.seeds):
            raise ZoningSeedError("seed diagnostics must sum to the number of generated seeds")

        cells = tuple((seed.row, seed.col) for seed in self.seeds)
        if len(cells) != len(set(cells)):
            raise ZoningSeedError("generated zoning seeds must occupy unique raster cells")


class DeterministicZoningSeedGenerator:
    """Select suitability-aware, non-repeating raster-cell seeds for zoning."""

    version = "1"
    rng_namespace = "zoning.seeds.v1"

    def generate(
        self,
        *,
        suitability: WeightedSuitabilityResult,
        context: RunContext,
        count: int,
    ) -> ZoningSeedSet:
        if not isinstance(suitability, WeightedSuitabilityResult):
            raise ZoningSeedError("suitability must be a WeightedSuitabilityResult")
        if not isinstance(context, RunContext):
            raise ZoningSeedError("context must be a RunContext")
        _require_positive_int("count", count)
        if context.working_srid != suitability.grid.working_srid:
            raise ZoningSeedError(
                "RunContext working_srid must match the suitability grid working_srid"
            )

        valid_flat = np.flatnonzero(suitability.valid_mask.reshape(-1))
        valid_count = int(valid_flat.size)
        if count > valid_count:
            raise ZoningSeedError(
                f"requested {count} zoning seeds but only {valid_count} valid cells are available"
            )

        scores_flat = suitability.scores.reshape(-1)
        positive_mask = scores_flat[valid_flat] > 0.0
        positive_flat = valid_flat[positive_mask]
        rng = context.rng(self.rng_namespace)

        weighted_count = min(count, int(positive_flat.size))
        selected_parts: list[np.ndarray] = []
        if weighted_count > 0:
            weights = np.asarray(scores_flat[positive_flat], dtype=np.float64)
            probabilities = weights / np.sum(weights, dtype=np.float64)
            weighted_selected = np.asarray(
                rng.choice(
                    positive_flat,
                    size=weighted_count,
                    replace=False,
                    p=probabilities,
                ),
                dtype=np.int64,
            )
            selected_parts.append(weighted_selected)

        fallback_count = count - weighted_count
        if fallback_count > 0:
            zero_score_flat = valid_flat[~positive_mask]
            fallback_selected = np.asarray(
                rng.choice(zero_score_flat, size=fallback_count, replace=False),
                dtype=np.int64,
            )
            selected_parts.append(fallback_selected)

        selected_flat = np.concatenate(selected_parts)
        selected_flat.sort()
        seeds = tuple(
            _seed_from_flat_index(
                flat_index=int(flat_index),
                suitability=suitability,
            )
            for flat_index in selected_flat.tolist()
        )
        return ZoningSeedSet(
            seeds=seeds,
            rng_namespace=self.rng_namespace,
            rng_seed=context.rng_seed(self.rng_namespace),
            weighted_seed_count=weighted_count,
            uniform_fallback_count=fallback_count,
        )


def _seed_from_flat_index(
    *,
    flat_index: int,
    suitability: WeightedSuitabilityResult,
) -> ZoningSeed:
    grid = suitability.grid
    row, col = divmod(flat_index, grid.width)
    min_x, _min_y, _max_x, max_y = grid.bounds
    x_m = min_x + (col + 0.5) * grid.cell_width_m
    y_m = max_y - (row + 0.5) * grid.cell_height_m
    return ZoningSeed(
        row=row,
        col=col,
        x_m=x_m,
        y_m=y_m,
        suitability_score=float(suitability.scores[row, col]),
    )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ZoningSeedError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ZoningSeedError(f"{field_name} must be a non-negative integer")
