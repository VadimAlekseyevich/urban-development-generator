# Run-scoped DEM source adapter (#202)

The normalized TIFF from ordinary ingest is stored at
`datasets/<version-id>/normalized/raster.tif`. The new
`SqlAlchemyDEMSourceReader.open(snapshot=..., grid=...)` context manager
resolves the exact ready DEM linked to the run and verifies the persisted
artifact ownership, size and SHA-256 (including the streamed bytes).
It does not open arbitrary paths or choose a different dataset version.

A bounded local scratch copy and a Rasterio WarpedVRT align the single-band
metric DEM to the requested suitability grid. Bilinear interpolation is used
for continuous elevations; outside coverage and NoData remain invalid.
Elevations must be in metres (if no vertical unit metadata is present, the
caller must have supplied metres); scaled/offset bands and multiple DEMs are
rejected rather than silently interpreted. The source is usable only inside
the context manager; use the existing DEMSlopeFactor within that scope.
Scratch size is limited to 2 GiB, target grid to 25 million cells and each
read to 1.1 million cells.

The PostGIS integration tests use actual TIFF bytes in a local ArtifactStore
with persisted ownership metadata, then evaluate the existing slope factor.
They do not yet prove ordinary upload-to-generation execution. The adapter
is not installed in the generation worker until the production composition
and remaining stages are ready. No changes to canonical Stage or Job models.
