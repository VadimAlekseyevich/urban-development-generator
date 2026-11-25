# Ingest worker lifecycle (S03-T09)

S03-T09 connects the ingest primitives from S03-T03..T08 to a retry-safe ARQ worker.
The database remains authoritative for both the `Job` attempt lifecycle and the immutable
`DatasetVersion` result lifecycle.

## DatasetVersion manifest

`DatasetVersion.source_metadata` is immutable and acts as the deterministic ingest manifest.
Redis does not carry GIS configuration that can drift between retries. The worker queue payload
only needs `job_id` and `dataset_version_id`.

Vector example:

```json
{
  "artifact_key": "uploads/8f.../roads.gpkg",
  "format": "gpkg",
  "layer": "roads"
}
```

Raster example:

```json
{
  "artifact_key": "uploads/31.../dem.tif",
  "format": "geotiff",
  "target_resolution_m": 20,
  "clip_bounds": [500000, 6100000, 510000, 6110000],
  "resampling": "bilinear"
}
```

Supported `format` values in this task are `geojson`, `gpkg`, `shapefile_zip`, and `geotiff`.
For vector datasets the canonical target is `Dataset.kind`: `roads`, `buildings`, `landuse`,
`water`, `facilities`, or `constraints`. Roads require line geometry; buildings and landuse
require polygon geometry. Geometry-generic canonical tables infer the family from OGR metadata,
or accept an explicit `geometry_family` (`point`, `line`, `polygon`) when OGR cannot advertise it.

Raster clip bounds are in the project's metric `working_srid`. Raster resampling is explicit and
uses the S03-T08 policy.

## State transitions

A worker claim locks both `Job` and `DatasetVersion` before changing state.

```text
Job:            queued/failed -> running -> succeeded
                                      \----> failed

DatasetVersion: uploaded/failed -> processing -> ready
                                      \-------> failed
```

A repeated delivery for an already `ready` DatasetVersion is a no-op success and does not increase
`attempt_count`. A recent `running` attempt is treated as in progress. A `running` attempt older
than the worker timeout safety window can be reclaimed after a process crash.

`Job.max_attempts` is authoritative. Transient failures are eligible for bounded exponential ARQ
retry only while the DB attempt budget remains. Data/config/permanent failures are recorded and
are not retried unchanged.

## Retry/idempotency guarantees

Vector ingest uses S03-T06 replace-on-retry. Each retry deletes and rewrites the canonical rows for
that DatasetVersion inside one Postgres transaction, so a failed attempt cannot accumulate duplicate
source rows.

Raster output uses a deterministic logical key:

```text
datasets/<dataset-version-id>/normalized/raster.tif
```

If a process fails after the GeoTIFF is promoted but before DB finalization, the next attempt reuses
that ready artifact instead of trying to overwrite it.

Successful DB finalization is one transaction: source and derived artifact metadata are marked
`referenced` with owner `dataset_version`, `DatasetVersion` becomes `ready`, and `Job` becomes
`succeeded`. DatasetVersion source metadata and checksum are never mutated by the worker.

## Resource boundaries

Vector files are materialized from `ArtifactStore` using fixed 1 MiB reads because GDAL needs random
access. Shapefile ZIP extraction still uses the S03-T03 security limits. Vector feature processing
remains bounded by S03-T05 batch sizes.

Raster normalization remains windowed/tiled and bounded by S03-T08 `max_output_samples`; a retry
does not read the complete raster into RAM.

S03-T09 intentionally does not add OSM PBF parsing or mapping. Those remain S03-T10..T12.
It also does not add source-layer HTTP/UI endpoints; those remain S03-T13..T14.
