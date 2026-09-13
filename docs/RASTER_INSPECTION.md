# Raster inspection (S03-T07)

`backend.app.services.raster_inspection.RasterInspector` is the metadata-only boundary for
uploaded raster sources before S03-T08 normalization.

## Contract

Inspection returns:

- GDAL/Rasterio driver;
- width, height, band count and per-band dtype;
- source CRS when present;
- the six affine transform coefficients `(a, b, c, d, e, f)`;
- per-band nodata values;
- pixel resolution derived from the affine basis vectors;
- full raster extent derived from the affine transform and raster dimensions.

Resolution and extent remain in source-CRS units. S03-T07 does not reproject, clip, resample,
calculate raster statistics, or interpret DEM values.

## Metadata-first behavior

`RasterioMetadataBackend` opens the datasource through Rasterio/GDAL and reads dataset
properties only. It never calls `DatasetReader.read()`, so inspection does not materialize a
full band or raster array in Python memory.

The inspector rejects non-positive dimensions/band counts, blank dtypes, invalid CRS metadata,
non-finite or degenerate affine transforms, inconsistent per-band metadata and infinite nodata
sentinels. `NaN` nodata is accepted because it is a valid floating-point raster sentinel.

A missing CRS is reported as `crs=None` rather than guessed. CRS requirements and reprojection
belong to S03-T08 raster normalization.

## Rotated rasters

Resolution is computed from affine basis-vector magnitudes, and extent is computed from all four
transformed raster corners. This keeps inspection correct for rotated/sheared affine transforms
instead of assuming north-up rasters.

## Out of scope

S03-T07 does not:

- read raster pixels;
- infer or override a missing CRS;
- reproject or resample data;
- clip to project bounds;
- produce GeoTIFF/COG artifacts.

Those operations are part of S03-T08 and later ingest orchestration.
