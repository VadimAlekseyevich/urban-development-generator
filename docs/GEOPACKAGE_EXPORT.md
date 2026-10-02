# S13-T08 — bounded multi-layer GeoPackage export job (UG-AI-088)

S13-T08 extends the existing asynchronous export path with one multi-layer
GeoPackage artifact. It reuses the canonical LayerCatalog, the S13 bounded
vector query service, the DB-authoritative `Job -> JobOutbox -> ARQ` lifecycle
and the storage-neutral `ArtifactStore`. GeoPackage generation never runs
inside an HTTP request.

## API

Create:

```http
POST /api/v1/projects/{project_id}/exports/geopackage
Content-Type: application/json

{
  "layer_ids": ["source.roads", "generated.buildings"],
  "dataset_version_id": "...",
  "run_id": "...",
  "bbox": [-0.2, 51.45, 0.0, 51.6],
  "max_features_per_layer": 100000,
  "max_total_features": 250000
}
```

The response is HTTP 202 with `geopackage-export-v1` state. Layer selection
is canonicalized into LayerCatalog order before the idempotency key is built,
so changing only request order does not create a different export job.

Status:

```http
GET /api/v1/projects/{project_id}/exports/geopackage/{job_id}
```

Download becomes available only after success:

```http
GET /api/v1/projects/{project_id}/exports/geopackage/{job_id}/download
```

The ready object is streamed as `application/geopackage+sqlite3` and has a
`.gpkg` filename.

## Layer and owner rules

Only the same 15 UUID-table-backed LayerCatalog definitions used by the bounded
vector API are supported. Project boundary, suitability raster and
`validation.violations` remain on their specialized contracts.

A request may combine source and run-owned layers:

- selecting any `source.*` layer requires exactly one ready
  `dataset_version_id`;
- selecting any run-owned layer requires exactly one succeeded `run_id`;
- an owner UUID must not be supplied when no selected layer uses that owner;
- every owner must belong to the requested project;
- generated demography/infrastructure-demand must have their published read
  model before the export is accepted.

The bbox is finite, non-wrapping EPSG:4326. A request selects 1–15 unique
layers. `max_features_per_layer` is 1–100,000 and
`max_total_features` is 1–500,000. Exceeding either cap fails the job rather
than silently truncating an artifact.

## GeoPackage representation

Each selected canonical layer becomes one GeoPackage layer whose name replaces
the catalog dot with an underscore, for example
`generated.buildings -> generated_buildings`. Selected layers with zero
features are still created.

S13-T08 intentionally uses a stable two-column attribute envelope:

- `feature_id` — canonical feature UUID as text;
- `properties_json` — deterministic JSON containing the complete canonical
  vector-read properties.

Geometry is written in EPSG:4326, matching the bounded vector presentation
contract. GeoPackage geometry type is `Unknown` so canonical mixed-geometry
layers remain valid without lossy coercion. Layer metadata stores the canonical
layer ID and export schema version.

This representation preserves all properties while keeping one append-stable
schema across batches whose source JSON attributes may have different keys.

## Bounded worker execution

The worker opens a fresh DB session for every keyset page and calls the existing
`VectorLayerQueryService` / `SqlAlchemyVectorLayerRepository`. A page is at
most 5,000 features. No selected layer or whole export is intentionally loaded
into memory at once.

Batches are converted to GeoDataFrames and appended with pyogrio/GDAL to a
temporary on-disk GeoPackage. After all layers complete, the file stream is
written through `ArtifactStore.put()` and promoted to ready.

The logical key is deterministic:

```text
exports/{project_id}/{job_id}/urban-layers-{job-prefix}.gpkg
```

Unlike GeoJSON, GeoPackage container bytes are not assumed to be byte-for-byte
deterministic across independent GDAL writes. If a worker dies after storage
promotion but before DB completion, retry first detects the ready object,
validates its exact selected layer set and feature counts against the immutable
request caps, and reuses it without regenerating the container.

## Persistence and artifact ownership

Migration `0025_geopackage_exports` stores the canonical layer list, exact
owner UUIDs, bbox, per-layer/total caps, final layer counts and optional
artifact FK. Generic `jobs` remains the lifecycle authority and
`job_outbox` emits one `run_geopackage_export` message on logical queue
`export`.

After successful publication, the DB Artifact row is transitioned through the
existing lifecycle and referenced with `owner_type=job` in the same
transaction that marks the job succeeded and stores per-layer counts.

Physical export queue separation is still S14-T01. S3/MinIO parity remains
S13-T11; no storage-vendor semantics enter this contract.

## Verification

Unit coverage asserts canonical layer ordering/idempotency, exact owner
requirements, hard per-layer/total bounds, empty selected layers, multi-layer
batch writing, ready-artifact recovery, retry taxonomy and worker registration.

PostGIS integration covers API create -> Job/GeoPackageExport/JobOutbox ->
worker -> referenced Artifact -> status/download, canonical layer ordering,
duplicate request idempotency, two physical GeoPackage layers, persisted feature
counts, project scoping and rejection of unpublished/non-table inputs.
