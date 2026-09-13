# Raster threshold constraint

S04-T04 adds a generic HARD raster threshold rule on top of the S04 constraint engine. It stays inside `core` and does not read GeoTIFF files directly, depend on Rasterio/GDAL adapters, or implement suitability scoring.

## Contract

`RasterThresholdConstraint` evaluates a `RasterThresholdSubject` containing one or more pixel `RasterWindow` values. The subject and raster source must use the same project metric `working_srid`; `TerritorySnapshot` and `RunContext` are checked against that SRID before evaluation.

The raster dependency is a minimal `RasterThresholdSource` port:

- `working_srid`;
- `width` / `height`;
- `read_window(window=...)` returning exactly the requested cells;
- numeric finite samples or `None` for nodata.

This keeps file access and GDAL/Rasterio concerns outside the domain rule while still making windowed access mandatory.

## Threshold semantics

`RasterThresholdPolicy` supports two deterministic comparisons:

- `AT_MOST`: values must be `<= threshold`;
- `AT_LEAST`: values must be `>= threshold`.

The first failing sample in window order, then row-major cell order, is reported. This is suitable for hard rules such as a maximum allowed slope raster once such a raster is prepared by a stage/adapter.

## Nodata policy

Nodata handling is explicit:

- `REJECT` fails on the first `None` sample;
- `IGNORE` skips nodata cells.

Even with `IGNORE`, a subject for which every requested cell is nodata fails closed with `NO_VALID_SAMPLES`; missing raster evidence is not silently treated as a pass.

## Bounded window policy

The policy has two independent limits:

- `max_windows` bounds the number of window reads per evaluation;
- `max_sample_cells` bounds the total requested cell count.

Both limits are checked before any source read. Windows are also checked against source dimensions. A limit breach raises `RasterThresholdSampleLimitError` instead of allowing an unbounded raster scan.

The source must return the exact requested shape. Malformed rows, non-numeric samples, NaN, or infinity are contract errors.

## CRS and units

`working_srid` is validated through the existing projected/metre CRS guard. S04-T04 does not reproject rasters and does not infer CRS. Pixel-window derivation from world coordinates belongs to stage/adapter preparation where the raster transform is known.

## Out of scope

S04-T04 intentionally does not add:

- DEM-to-slope derivation (S04-T07);
- suitability factor weights or normalization (S04-T05+);
- hard exclusion raster masks (S04-T06);
- raster artifact persistence or map visualization (S04-T11..T12);
- Rasterio/GDAL file adapters inside `core`.
