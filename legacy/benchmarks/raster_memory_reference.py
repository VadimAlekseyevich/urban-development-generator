"""Windowed raster normalization reference profile, with observed Linux peak RSS.

Unlike mock-array microbenchmarks, this exercises the real LocalArtifactStore,
RasterNormalizer, Rasterio WarpedVRT and tiled GeoTIFF output pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import resource
import tempfile
from pathlib import Path
from time import perf_counter

import numpy as np
import rasterio
from rasterio.transform import from_origin

from backend.app.adapters import LocalArtifactStore
from backend.app.services.raster_normalization import (
    RasterNormalizationConfig,
    RasterNormalizer,
    RasterResampling,
)
from core.urban_generator.domain import ArtifactRef, WorkingCRS

PROFILE_SCHEMA = "s14-raster-memory-v1"
MAX_REFERENCE_CELLS = 25_000_000


def _current_rss_mib() -> float | None:
    try:
        with open("/proc/self/status", encoding="ascii") as source:
            for line in source:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def _peak_rss_mib() -> float:
    # ru_maxrss is KiB on Linux and bytes on macOS.
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024.0 if platform.system() == "Linux" else 1024.0**2)


def _write_windowed_input(path: Path, *, width: int, height: int, tile_size: int) -> None:
    """Generate a large GeoTIFF using one tile; never allocate a full raster."""

    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height,
        count=1, dtype="float32", crs="EPSG:3857",
        transform=from_origin(0.0, height * 10.0, 10.0, 10.0),
        nodata=-9999.0, tiled=True,
        blockxsize=tile_size, blockysize=tile_size,
        compress="DEFLATE",
    ) as destination:
        source_tile = (
            np.arange(tile_size * tile_size, dtype=np.float32)
            .reshape(tile_size, tile_size) % 2000
        )
        for _, window in destination.block_windows(1):
            height_px = int(window.height)
            width_px = int(window.width)
            destination.write(
                source_tile[:height_px, :width_px], 1, window=window
            )


def run_raster_memory_profile(
    *,
    width: int,
    height: int,
    tile_size: int = 256,
    gdal_cache_mb: int = 64,
) -> dict[str, object]:
    """Return measured process memory and authoritative window diagnostics."""

    for name, value in (("width", width), ("height", height)):
        if type(value) is not int or not 128 <= value <= 8192:
            raise ValueError(f"{name} must be an integer between 128 and 8192")
    if width * height > MAX_REFERENCE_CELLS:
        raise ValueError("fixture exceeds the canonical 25 million-cell reference limit")
    config = RasterNormalizationConfig(
        tile_size=tile_size,
        resampling=RasterResampling.BILINEAR,
        max_output_samples=MAX_REFERENCE_CELLS,
    )
    normalizer = RasterNormalizer(gdal_cache_mb=gdal_cache_mb)

    with tempfile.TemporaryDirectory(prefix="udg-raster-memory-reference-") as root:
        path = Path(root)
        input_path = path / "input.tif"
        _write_windowed_input(
            input_path, width=width, height=height, tile_size=config.tile_size
        )
        store = LocalArtifactStore(path / "artifact-store")
        temp_ref = ArtifactRef(key="reference/input.tif")
        with input_path.open("rb") as source:
            source_ref = store.put(temp_ref, source, content_type="image/tiff")
        input_ref = store.promote(source_ref.ref).ref

        before_current = _current_rss_mib()
        before_peak = _peak_rss_mib()
        start = perf_counter()
        result = normalizer.normalize(
            store,
            source_ref=input_ref,
            output_ref=ArtifactRef(key="reference/normalized.tif"),
            working_crs=WorkingCRS(3857),
            config=config,
        )
        duration_ms = (perf_counter() - start) * 1000.0
        after_current = _current_rss_mib()
        after_peak = _peak_rss_mib()

        expected_windows = (
            math.ceil(width / tile_size) * math.ceil(height / tile_size)
        )
        if (result.width, result.height, result.band_count) != (width, height, 1):
            raise AssertionError("normalized output dimensions/bands changed")
        if result.windows_written != expected_windows:
            raise AssertionError("normalizer did not write the exact tile count")
        if result.max_window_pixels > tile_size * tile_size:
            raise AssertionError("normalization read a larger-than-allowed block")
        with store.open(result.stat.ref) as artifact:
            # Rasterio verifies geometry/bands without reading all pixels.
            with tempfile.NamedTemporaryFile(suffix=".tif", dir=path) as copy:
                while chunk := artifact.read(1024 * 1024):
                    copy.write(chunk)
                copy.flush()
                with rasterio.open(copy.name) as normalized:
                    if normalized.count != 1 or normalized.crs.to_epsg() != 3857:
                        raise AssertionError("unexpected normalized GeoTIFF metadata")

        return {
            "profile_schema": PROFILE_SCHEMA,
            "fixture": "synthetic-tiled-input-v1",
            "python": platform.python_version(),
            "rasterio": rasterio.__version__,
            "width": width,
            "height": height,
            "sample_count": width * height,
            "tile_size": tile_size,
            "gdal_cache_mb": gdal_cache_mb,
            "source_size_bytes": result.source_size_bytes,
            "output_size_bytes": result.stat.size_bytes,
            "windows_written": result.windows_written,
            "max_window_pixels": result.max_window_pixels,
            "normalization_ms": round(duration_ms, 3),
            "rss_before_mib": before_current,
            "rss_after_mib": after_current,
            "rss_peak_before_mib": round(before_peak, 3),
            "rss_peak_after_mib": round(after_peak, 3),
            "rss_peak_growth_mib": round(max(0.0, after_peak - before_peak), 3),
            "rss_measurement": "linux-ru_maxrss-process" if platform.system() == "Linux"
                else "platform-ru_maxrss-process",
            "ci_machine_memory_limit": os.environ.get("MEMORY_LIMIT", "unspecified"),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=2048)
    parser.add_argument("--height", type=int, default=2048)
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--gdal-cache-mb", type=int, default=64)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_raster_memory_profile(
        width=args.width, height=args.height,
        tile_size=args.tile_size, gdal_cache_mb=args.gdal_cache_mb,
    )
    print(json.dumps(report, indent=2))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
