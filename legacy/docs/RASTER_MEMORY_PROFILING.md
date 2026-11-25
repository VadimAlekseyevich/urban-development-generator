# S14-T04 — Measured raster peak memory and windowing

**Ordered task:** UG-AI-098. This gate measures the *actual* Rasterio
normalization pipeline, audits other raster paths and removes one avoidable
full-grid bytes allocation from suitability Stage fingerprinting.

## Repeatable benchmark

```sh
uv run python -m benchmarks.raster_memory_reference \
  --width 2048 --height 2048 --tile-size 256 \
  --gdal-cache-mb 64 --output /tmp/udg-raster-memory.json
```

`benchmarks/raster_memory_reference.py` creates a synthetic, deterministic,
tiled EPSG:3857 single-band GeoTIFF from **one reusable input tile**, stages
it through the real `LocalArtifactStore`, runs
`RasterNormalizer.normalize()` (disk spool → WarpedVRT → per-band
destination blocks → ready Artifact), verifies raster metadata without a
full-band read, and deletes its temporary files.

The JSON evidence contains input/output byte sizes, width/height/bands,
raster window count, largest read window, elapsed normalization time,
GDAL cache cap, Python/Rasterio version, RSS just before/after normalization,
and **process high-water RSS** measured using Linux `ru_maxrss`. A
`rss_peak_growth_mib` value is also provided. Memory readings include the
Python interpreter, library imports, input fixture generation and the
ArtifactStore: **they are not the size of one Rasterio window**, and
are not directly comparable between runner hardware. The high-water figure
may already have risen during fixture creation.

The Python CI job runs a 2048×2048 reference case and retains the JSON as
the `raster-memory-profile` artifact (14 days). Unit tests exercise
an uneven 256×320 grid, verify the exact window count and bounds,
and reject configurations exceeding the established 25M-cell reference
ceiling. This is **not** an accepted 25/100 km² load benchmark or a
memory SLO; those belong to S14-T07.

## Corrections and invariant proof

1. **Rasterizer:** the existing `RasterNormalizer` already spooled
   input blobs in chunks and read exactly **one destination tile × one
   band** from the WarpedVRT. It now encloses the warp inside a bounded
   Rasterio GDAL cache (`gdal_cache_mb=64`, validated 1–256 MB).
   Destination window diagnostics remain
   `max_window_pixels <= tile_size²`, and the output sample budget
   is checked before any output raster blocks are read.
2. **Canonical suitability fingerprint:** the old Stage implementation
   repeatedly used `array.tobytes(order="C")` to feed the stage digest,
   creating a temporary full-grid bytes copy per hard mask, factor value
   array, factor validity mask, output score grid and final validity mask.
   It now uses one `StageFingerprintWriter` and bounded buffered
   NumPy external loops (8192 elements per chunk). The **v1 hash protocol**
   remains unchanged: each typed part has a marker, 8-byte big-endian
   byte length, then exact C-order content. Unit tests compare digest
   outputs to the old bytes-based reference across float64, float32,
   boolean, noncontiguous and odd-shaped arrays, including chunk >8192.
   Zero-length and declared-length mismatches are explicitly guarded.
3. **Early reject:** `SuitabilityStage.execute` checks
   `config.max_cells` **before** running any factor, so a submitted grid
   larger than the domain cap cannot allocate full-grid factor arrays
   merely to be rejected by the aggregator afterward.
4. **Preview/inspection:** `RasterInspector` reads only metadata.
   `SuitabilityLayerService.render_preview` reads bounded resampled
   output arrays using `out_shape` and enforces the configured preview
   dimension 128–4096, with default 2048. It necessarily holds the
   bounded PNG preview arrays in memory, not the source-resolution raster.
5. **Known intentional full arrays:** suitability factor implementations
   and `WeightedSuitabilityResult` must currently own full dense grids;
   `SuitabilityArtifactWriter._build_statistics` copies the valid-score
   subset to compute **exact** percentiles. These are *not* proven
   constant-memory stages. Their aggregate peak requires separate
   real-territory tests before lifting the canonical 25M-cell cap.
   This task does not substitute approximate quantiles or silently
   change canonical metrics.

## Release interpretation

We can say that **normalization I/O is windowed and GDAL cache-bounded**,
and Stage fingerprinting no longer duplicates whole raster byte buffers.
We **cannot** claim that every raster stage is fully streaming or that
100 km² processing is production-safe. Retain the raw RSS evidence and
benchmark at larger, representative spatial extents during S14-T07.
