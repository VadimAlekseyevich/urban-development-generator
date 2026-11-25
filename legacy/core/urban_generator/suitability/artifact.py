from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.windows import Window

from core.urban_generator.domain.artifacts import (
    ArtifactRef,
    ArtifactStat,
    ArtifactStore,
    require_temporary_artifact_ref,
)
from core.urban_generator.suitability.aggregation import (
    WeightedSuitabilityError,
    WeightedSuitabilityResult,
)
from core.urban_generator.suitability.config import SuitabilityConfig
from core.urban_generator.suitability.hard_exclusion import HardExclusionMask

_CONTENT_TYPE = "image/tiff"
_SCHEMA_VERSION = "suitability-artifact-v1"
_DEFAULT_TILE_SIZE = 512
_MAX_TILE_SIZE = 4096
_DEFAULT_MAX_CELLS = 25_000_000


class SuitabilityArtifactError(WeightedSuitabilityError):
    """Raised when a suitability raster artifact violates the canonical contract."""


class SuitabilityCellStatus(IntEnum):
    """Canonical status codes stored in GeoTIFF band 2."""

    INVALID_DATA = 0
    VALID = 1
    HARD_EXCLUDED = 2


@dataclass(frozen=True, slots=True)
class SuitabilityFactorProvenance:
    """Configuration and implementation identity for one factor."""

    code: str
    version: str
    weight: float
    normalization: str
    raw_min: float | None
    raw_max: float | None


@dataclass(frozen=True, slots=True)
class SuitabilityArtifactProvenance:
    """Stable provenance embedded into and returned with the canonical raster."""

    schema_version: str
    config_version: str
    config_fingerprint: str
    factors: tuple[SuitabilityFactorProvenance, ...]
    hard_exclusion_source_codes: tuple[str, ...]
    working_srid: int
    bounds: tuple[float, float, float, float]
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class SuitabilityArtifactStatistics:
    """Deterministic summary statistics for valid suitability cells."""

    total_cells: int
    valid_cells: int
    hard_excluded_cells: int
    invalid_data_cells: int
    minimum_score_threshold: float
    preferred_score_threshold: float | None
    meets_minimum_cells: int
    preferred_cells: int | None
    min_score: float | None
    max_score: float | None
    mean_score: float | None
    p05_score: float | None
    p50_score: float | None
    p95_score: float | None


@dataclass(frozen=True, slots=True)
class SuitabilityArtifact:
    """Ready canonical GeoTIFF plus statistics and provenance."""

    stat: ArtifactStat
    statistics: SuitabilityArtifactStatistics
    provenance: SuitabilityArtifactProvenance
    windows_written: int
    max_window_cells: int


class SuitabilityArtifactWriter:
    """Persist weighted suitability as a canonical two-band GeoTIFF artifact.

    Band 1 stores the score in ``0..1``. Band 2 stores ``SuitabilityCellStatus`` codes.
    The input result already owns the full raster arrays, but serialization creates only
    bounded per-window copies before streaming the finished temporary GeoTIFF to ArtifactStore.
    """

    def __init__(
        self,
        *,
        tile_size: int = _DEFAULT_TILE_SIZE,
        max_cells: int = _DEFAULT_MAX_CELLS,
    ) -> None:
        _require_tile_size(tile_size)
        _require_positive_int("max_cells", max_cells)
        self._tile_size = tile_size
        self._max_cells = max_cells

    def write(
        self,
        store: ArtifactStore,
        *,
        output_ref: ArtifactRef,
        result: WeightedSuitabilityResult,
        config: SuitabilityConfig,
        hard_mask: HardExclusionMask,
    ) -> SuitabilityArtifact:
        if not isinstance(store, ArtifactStore):
            raise SuitabilityArtifactError("store must implement ArtifactStore")
        require_temporary_artifact_ref(output_ref)
        _validate_inputs(result=result, config=config, hard_mask=hard_mask)
        if result.grid.cell_count > self._max_cells:
            raise SuitabilityArtifactError(
                "suitability artifact grid cell limit exceeded: "
                f"{result.grid.cell_count} > {self._max_cells}"
            )

        statistics = _build_statistics(result=result, config=config)
        provenance = _build_provenance(
            result=result,
            config=config,
            hard_mask=hard_mask,
        )
        ready_ref = output_ref.as_ready()
        windows_written = 0
        max_window_cells = 0

        try:
            with tempfile.TemporaryDirectory(prefix="urban-suitability-artifact-") as temp_dir:
                output_path = Path(temp_dir) / "suitability.tif"
                windows_written, max_window_cells = self._write_geotiff(
                    output_path=output_path,
                    result=result,
                    statistics=statistics,
                    provenance=provenance,
                )
                with output_path.open("rb") as source:
                    store.put(output_ref, source, content_type=_CONTENT_TYPE)
                ready_stat = store.promote(output_ref)
        except Exception:
            _best_effort_delete(store, output_ref)
            _best_effort_delete(store, ready_ref)
            raise

        return SuitabilityArtifact(
            stat=ready_stat,
            statistics=statistics,
            provenance=provenance,
            windows_written=windows_written,
            max_window_cells=max_window_cells,
        )

    def _write_geotiff(
        self,
        *,
        output_path: Path,
        result: WeightedSuitabilityResult,
        statistics: SuitabilityArtifactStatistics,
        provenance: SuitabilityArtifactProvenance,
    ) -> tuple[int, int]:
        grid = result.grid
        transform = from_bounds(*grid.bounds, width=grid.width, height=grid.height)
        windows_written = 0
        max_window_cells = 0

        with rasterio.open(
            output_path,
            "w",
            driver="GTiff",
            width=grid.width,
            height=grid.height,
            count=2,
            dtype="float32",
            crs=f"EPSG:{grid.working_srid}",
            transform=transform,
            tiled=True,
            blockxsize=self._tile_size,
            blockysize=self._tile_size,
            compress="DEFLATE",
            predictor=3,
        ) as dataset:
            dataset.set_band_description(1, "suitability_score")
            dataset.set_band_description(2, "cell_status")
            dataset.update_tags(
                schema_version=_SCHEMA_VERSION,
                status_codes_json=_canonical_json(
                    {
                        "invalid_data": int(SuitabilityCellStatus.INVALID_DATA),
                        "valid": int(SuitabilityCellStatus.VALID),
                        "hard_excluded": int(SuitabilityCellStatus.HARD_EXCLUDED),
                    }
                ),
                statistics_json=_canonical_json(_statistics_payload(statistics)),
                provenance_json=_canonical_json(_provenance_payload(provenance)),
            )

            for row_off in range(0, grid.height, self._tile_size):
                height = min(self._tile_size, grid.height - row_off)
                row_slice = slice(row_off, row_off + height)
                for col_off in range(0, grid.width, self._tile_size):
                    width = min(self._tile_size, grid.width - col_off)
                    col_slice = slice(col_off, col_off + width)
                    window = Window(col_off, row_off, width, height)

                    score_tile = np.asarray(
                        result.scores[row_slice, col_slice],
                        dtype=np.float32,
                    )
                    valid_tile = result.valid_mask[row_slice, col_slice]
                    hard_tile = result.hard_excluded_mask[row_slice, col_slice]
                    status_tile = np.full(
                        (height, width),
                        float(SuitabilityCellStatus.INVALID_DATA),
                        dtype=np.float32,
                    )
                    status_tile[valid_tile] = float(SuitabilityCellStatus.VALID)
                    status_tile[hard_tile] = float(SuitabilityCellStatus.HARD_EXCLUDED)

                    dataset.write(score_tile, 1, window=window)
                    dataset.write(status_tile, 2, window=window)
                    windows_written += 1
                    max_window_cells = max(max_window_cells, height * width)

        return windows_written, max_window_cells


def _validate_inputs(
    *,
    result: WeightedSuitabilityResult,
    config: SuitabilityConfig,
    hard_mask: HardExclusionMask,
) -> None:
    if not isinstance(result, WeightedSuitabilityResult):
        raise SuitabilityArtifactError("result must be a WeightedSuitabilityResult")
    if not isinstance(config, SuitabilityConfig):
        raise SuitabilityArtifactError("config must be a SuitabilityConfig")
    if not isinstance(hard_mask, HardExclusionMask):
        raise SuitabilityArtifactError("hard_mask must be a HardExclusionMask")
    if result.grid != hard_mask.grid:
        raise SuitabilityArtifactError("hard mask must use exactly the weighted result grid")
    if not np.array_equal(result.hard_excluded_mask, hard_mask.excluded):
        raise SuitabilityArtifactError("hard mask cells must match the weighted result")
    if result.config_version != config.version:
        raise SuitabilityArtifactError("config version must match the weighted result")
    if result.config_fingerprint != config.fingerprint:
        raise SuitabilityArtifactError("config fingerprint must match the weighted result")

    positive_codes = tuple(factor.code for factor in config.factors if factor.weight > 0.0)
    result_codes = tuple(code for code, _version in result.factor_versions)
    if result_codes != positive_codes:
        raise SuitabilityArtifactError(
            "weighted result factor order must match positive-weight config factors"
        )


def _build_statistics(
    *,
    result: WeightedSuitabilityResult,
    config: SuitabilityConfig,
) -> SuitabilityArtifactStatistics:
    valid_scores = result.scores[result.valid_mask]
    minimum = config.thresholds.minimum_score
    preferred = config.thresholds.preferred_score

    if valid_scores.size:
        min_score = float(np.min(valid_scores))
        max_score = float(np.max(valid_scores))
        mean_score = float(np.mean(valid_scores, dtype=np.float64))
        p05, p50, p95 = (
            float(value)
            for value in np.quantile(valid_scores, (0.05, 0.50, 0.95), method="linear")
        )
        meets_minimum = int(np.count_nonzero(valid_scores >= minimum))
        preferred_cells = (
            int(np.count_nonzero(valid_scores >= preferred))
            if preferred is not None
            else None
        )
    else:
        min_score = None
        max_score = None
        mean_score = None
        p05 = None
        p50 = None
        p95 = None
        meets_minimum = 0
        preferred_cells = 0 if preferred is not None else None

    return SuitabilityArtifactStatistics(
        total_cells=result.grid.cell_count,
        valid_cells=result.valid_count,
        hard_excluded_cells=result.hard_excluded_count,
        invalid_data_cells=result.invalid_data_count,
        minimum_score_threshold=minimum,
        preferred_score_threshold=preferred,
        meets_minimum_cells=meets_minimum,
        preferred_cells=preferred_cells,
        min_score=min_score,
        max_score=max_score,
        mean_score=mean_score,
        p05_score=p05,
        p50_score=p50,
        p95_score=p95,
    )


def _build_provenance(
    *,
    result: WeightedSuitabilityResult,
    config: SuitabilityConfig,
    hard_mask: HardExclusionMask,
) -> SuitabilityArtifactProvenance:
    versions = dict(result.factor_versions)
    factors = tuple(
        SuitabilityFactorProvenance(
            code=factor.code,
            version=versions[factor.code],
            weight=factor.weight,
            normalization=factor.normalization.value,
            raw_min=factor.raw_min,
            raw_max=factor.raw_max,
        )
        for factor in config.factors
        if factor.weight > 0.0
    )
    grid = result.grid
    return SuitabilityArtifactProvenance(
        schema_version=_SCHEMA_VERSION,
        config_version=result.config_version,
        config_fingerprint=result.config_fingerprint,
        factors=factors,
        hard_exclusion_source_codes=hard_mask.source_codes,
        working_srid=grid.working_srid,
        bounds=grid.bounds,
        width=grid.width,
        height=grid.height,
    )


def _statistics_payload(statistics: SuitabilityArtifactStatistics) -> dict[str, object]:
    return {
        "total_cells": statistics.total_cells,
        "valid_cells": statistics.valid_cells,
        "hard_excluded_cells": statistics.hard_excluded_cells,
        "invalid_data_cells": statistics.invalid_data_cells,
        "minimum_score_threshold": statistics.minimum_score_threshold,
        "preferred_score_threshold": statistics.preferred_score_threshold,
        "meets_minimum_cells": statistics.meets_minimum_cells,
        "preferred_cells": statistics.preferred_cells,
        "min_score": statistics.min_score,
        "max_score": statistics.max_score,
        "mean_score": statistics.mean_score,
        "p05_score": statistics.p05_score,
        "p50_score": statistics.p50_score,
        "p95_score": statistics.p95_score,
    }


def _provenance_payload(provenance: SuitabilityArtifactProvenance) -> dict[str, object]:
    return {
        "schema_version": provenance.schema_version,
        "config_version": provenance.config_version,
        "config_fingerprint": provenance.config_fingerprint,
        "factors": [
            {
                "code": factor.code,
                "version": factor.version,
                "weight": factor.weight,
                "normalization": factor.normalization,
                "raw_min": factor.raw_min,
                "raw_max": factor.raw_max,
            }
            for factor in provenance.factors
        ],
        "hard_exclusion_source_codes": list(provenance.hard_exclusion_source_codes),
        "grid": {
            "working_srid": provenance.working_srid,
            "bounds": list(provenance.bounds),
            "width": provenance.width,
            "height": provenance.height,
        },
    }


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _require_tile_size(value: int) -> None:
    _require_positive_int("tile_size", value)
    if value < 16 or value > _MAX_TILE_SIZE or value % 16 != 0:
        raise SuitabilityArtifactError(
            f"tile_size must be a multiple of 16 between 16 and {_MAX_TILE_SIZE}"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SuitabilityArtifactError(f"{field_name} must be a positive integer")


def _best_effort_delete(store: ArtifactStore, ref: ArtifactRef) -> None:
    try:
        store.delete(ref)
    except Exception:
        return
