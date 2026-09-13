# Raster normalization (S03-T08)

`backend.app.services.raster_normalization.RasterNormalizer` converts one immutable uploaded raster artifact into a normalized, tiled GeoTIFF artifact suitable for later ingest stages.

## Contract

The input is a `READY` `ArtifactRef` and an `ArtifactStore`; the output target must be a different `TEMPORARY` `ArtifactRef`. The caller supplies the project's validated `WorkingCRS` and a `RasterNormalizationConfig`.

Normalization performs:

1. bounded chunked spooling of the source artifact to worker-local temporary storage;
2. source CRS validation;
3. target-grid calculation in the project's metric working CRS;
4. optional clipping, with `clip_bounds` explicitly interpreted in the target CRS;
5. windowed reprojection/resampling through Rasterio `WarpedVRT`;
6. tiled, DEFLATE-compressed GeoTIFF creation;
7. `ArtifactStore.put()` followed by storage promotion to `READY`.

The local scratch directory is always removed when the operation exits. If output storage/promotion fails, both temporary and ready output refs are deleted best-effort.

## Memory and work bounds

The source artifact is never materialized as one in-memory byte string. The spooler uses fixed-size reads (1 MiB by default).

Raster reprojection is also bounded: destination block windows are visited one at a time and one band is read for each window. `tile_size` defaults to 512 pixels, so an application-level read never requests the complete raster array.

`max_output_samples` (default `100,000,000`) bounds `output_width × output_height × band_count` before the destination raster is created. This prevents an accidental tiny target resolution from creating an unbounded job.

## CRS, clipping, and resolution

The source raster must have a usable CRS. The destination CRS is always the supplied metric `WorkingCRS`; no hidden CRS default exists.

`target_resolution_m` is optional. When present it is a positive finite size in metres. When absent, GDAL/Rasterio derives a target resolution while transforming the source extent.

`clip_bounds` has the order `(left, bottom, right, top)` and is expressed in the target working CRS. It is intersected with transformed source coverage. A non-overlapping clip is rejected. If clip edges do not align exactly to the target pixel size, the output grid covers the requested intersection and may extend by less than one pixel on its right/bottom edge.

## Resampling and nodata

The supported explicit resampling methods are `nearest` (default), `bilinear`, `cubic`, and `average`. Callers must select interpolation appropriate to dataset semantics. The normalizer does not infer whether a raster is categorical or continuous.

Per-band dtypes and nodata metadata must be uniform because the normalized GeoTIFF contract uses one dataset dtype/nodata value. `NaN` nodata is supported; infinite nodata is rejected. A source with no nodata keeps nodata undefined.

## Output layout

The normalized artifact is a regular GeoTIFF with project working CRS, explicit affine transform/resolution, tiled blocks (`512 × 512` by default), DEFLATE compression, BigTIFF `IF_SAFER` creation policy, and normalization provenance tags.

This is **COG-friendly**, not yet a promise that every output satisfies the full Cloud Optimized GeoTIFF layout/overview rules. Overview generation or a dedicated COG driver can be added when remote/range-read serving requires it.

## Deliberate S03-T08 boundary

This task does not change `DatasetVersion` lifecycle state, create worker jobs, or bind the normalized artifact to a database `Artifact` owner. S03-T09 owns ingest job orchestration, retry/idempotency, `uploaded -> processing -> ready/failed`, and persistence of normalized-ingest provenance.
