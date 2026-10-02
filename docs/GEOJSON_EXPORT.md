# S13-T07 — asynchronous GeoJSON export job (UG-AI-087)

S13-T07 adds one asynchronous, single-layer GeoJSON export path over the
canonical 15 table-backed LayerCatalog definitions. It reuses the S13 bounded
vector read model, the existing DB-authoritative `Job -> JobOutbox -> ARQ`
delivery contract and the storage-neutral `ArtifactStore`. It does not add a
second GIS query path or perform export work inside an HTTP request.

## API

Create:

```http
POST /api/v1/projects/{project_id}/exports/geojson
Content-Type: application/json

{
  "layer_id": "source.roads",
  "dataset_version_id": "...",
  "run_id": null,
  "bbox": [-0.2, 51.45, 0.0, 51.6],
  "max_features": 100000
}
```

The response is HTTP 202 with `geojson-export-v1` job state. The exact same
normalized request is idempotent per project and returns the existing job.

Status:

```http
GET /api/v1/projects/{project_id}/exports/geojson/{job_id}
```

Download becomes available only after success:

```http
GET /api/v1/projects/{project_id}/exports/geojson/{job_id}/download
```

The download streams the ready ArtifactStore object as
`application/geo+json`; the API never exposes a filesystem path or storage
vendor URI.

## Exact owner and snapshot rules

A request exports exactly one of the same 15 UUID-table-backed definitions used
by the bounded vector API:

- six `source.*` layers require exactly one `dataset_version_id`;
- nine run projections require exactly one `run_id`;
- project boundary, suitability raster and integer-index validation violations
  remain on their specialized contracts and are rejected here.

Creation requires a **ready DatasetVersion** or a **succeeded GenerationRun** in
the requested project. Generated demography/infrastructure-demand additionally
must have their published read model. This makes the export input immutable
before it is queued. No active-run snapshot is claimed and no owner fallback is
allowed.

The bbox is mandatory EPSG:4326, non-wrapping and finite. `max_features` is a
hard 1–100,000 safety cap. Reaching the cap while another keyset page exists is
a failed export, never a silently truncated artifact.

## Persistence and queueing

Migration `0024_geojson_exports` stores immutable request metadata separately
from generic job state:

- `jobs`: authoritative queued/running/succeeded/failed state and retry count;
- `geojson_exports`: layer, exact owner UUID, bbox, feature cap, final feature
  count and optional artifact FK;
- `job_outbox`: one `run_geojson_export` message with stable
  `job:<job_id>` enqueue identity.

The logical queue name is `export`, but S13 still routes it to the existing
single physical ARQ queue. Physical queue separation/concurrency remains
S14-T01.

## Worker and ArtifactStore behavior

The worker opens a fresh bounded DB session for every keyset page and reuses
`VectorLayerQueryService` / `SqlAlchemyVectorLayerRepository`. Pages are at
most 5,000 features and keep exact owner/bbox scope. Geometry is exported in
RFC 7946 EPSG:4326 without render simplification.

Serialization writes incrementally to a `SpooledTemporaryFile` (8 MiB memory
threshold, then disk) and then passes the stream to `ArtifactStore.put()`.
The full FeatureCollection is never intentionally materialized as one Python
bytes/string object.

The logical storage key is deterministic:

```text
exports/{project_id}/{job_id}/{layer-id}-{job-prefix}.geojson
```

After `put -> promote`, DB metadata transitions
`temporary -> ready -> referenced` with `owner_type=job` in the same
transaction that marks the job succeeded and records `artifact_id` /
`feature_count`.

Storage and PostgreSQL still do not form one distributed transaction. If a
process dies after blob promotion but before DB completion, retry regenerates
the deterministic bytes, compares checksum/size/content type with the existing
ready object and reuses it rather than creating a second artifact. A permanently
orphaned export blob that never reaches DB ownership is a storage-GC concern;
generalized bounded artifact GC remains S14-T09.

Multi-layer GeoPackage export is implemented separately by S13-T08; see
`docs/GEOPACKAGE_EXPORT.md`. This GeoJSON contract remains intentionally
single-layer.

## Non-goals

This task intentionally does not implement:

- metrics CSV or config/provenance exports (S13-T09/T10);
- validation-report/project-boundary/raster conversion to GeoJSON;
- arbitrary output CRS, unbounded feature counts or active-run snapshots;
- physical export worker queue separation (S14-T01);
- S3/MinIO storage (S13-T11).

## Verification

Unit tests cover exact owner validation, deterministic idempotency, bounded
keyset streaming, checksum-stable ready-blob reuse, hard feature-cap failure,
worker registration and export outbox allowlisting.

The PostGIS integration test covers
API create -> persisted Job/GeoJsonExport/JobOutbox -> worker execution ->
referenced Artifact -> project-scoped status/download, duplicate request
idempotency, immutable-owner rejection and non-tabular layer rejection.
