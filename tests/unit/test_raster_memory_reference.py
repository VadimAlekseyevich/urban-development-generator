"""Memory baseline uses real Rasterio/ArtifactStore paths rather than mock arrays."""

from __future__ import annotations

import pytest

from backend.app.services.raster_normalization import RasterNormalizer
from benchmarks.raster_memory_reference import (
    PROFILE_SCHEMA,
    run_raster_memory_profile,
)


def test_tiled_raster_profile_measures_real_warp_and_peak_rss() -> None:
    profile = run_raster_memory_profile(
        width=256, height=320, tile_size=64, gdal_cache_mb=8,
    )
    assert profile["profile_schema"] == PROFILE_SCHEMA
    assert profile["fixture"] == "synthetic-tiled-input-v1"
    assert profile["windows_written"] == 20
    assert profile["max_window_pixels"] <= 64 * 64
    assert profile["sample_count"] == 256 * 320
    assert profile["source_size_bytes"] > 0
    assert profile["output_size_bytes"] > 0
    assert profile["rss_peak_after_mib"] > 0
    assert profile["rss_peak_growth_mib"] >= 0
    assert profile["normalization_ms"] >= 0


@pytest.mark.parametrize("kwargs", [
    {"width": 0, "height": 256},
    {"width": 9000, "height": 128},
    {"width": 8192, "height": 8192},
    {"width": True, "height": 256},
])
def test_benchmark_rejects_invalid_or_unbounded_raster(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        run_raster_memory_profile(**kwargs)


@pytest.mark.parametrize("cache", [-1, 0, 257, True, 2.5])
def test_gdal_cache_limit_requires_bounded_mb(cache: object) -> None:
    with pytest.raises(ValueError, match="gdal_cache_mb"):
        RasterNormalizer(gdal_cache_mb=cache)  # type: ignore[arg-type]
