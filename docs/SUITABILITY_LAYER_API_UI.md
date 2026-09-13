# Suitability layer API/UI

S04-T12 exposes the canonical S04-T11 suitability artifact as a bounded map preview and renders it in the existing MapLibre source-layers UI.

## Artifact lookup

The public API accepts the UUID of a row in `artifacts`, not a filesystem path or logical storage key. The row must be in `ready` or `referenced` state. Its `artifact://...` URI is resolved internally through `ArtifactStore`.

Before any raster metadata is returned, the service checks that the blob is a canonical `suitability-artifact-v1` GeoTIFF: two float32 bands, expected band descriptions, status-code tags, config/factor provenance, statistics, CRS, grid dimensions and extent must agree. An arbitrary uploaded TIFF therefore is not treated as a suitability result just because it is a raster.

### Metadata

```http
GET /api/v1/suitability-artifacts/{artifact_id}
```

The response contains:

- immutable artifact checksum/size/content type;
- suitability schema/config versions and config fingerprint;
- metric working CRS, grid bounds and raster dimensions;
- four WGS84 image corners plus a WGS84 bounding box for MapLibre;
- valid/hard-excluded/invalid-data cell counts;
- minimum/preferred threshold counts and score summary statistics;
- factor code/version/weight/normalization metadata;
- hard-exclusion source codes.

### PNG preview

```http
GET /api/v1/suitability-artifacts/{artifact_id}/preview.png?max_dimension=2048
```

`max_dimension` is restricted to `128..4096`. The source GeoTIFF is never read into memory as one blob: `ArtifactStore` bytes are copied to a bounded temporary file, and Rasterio reads only the two bands at the requested output shape. The generated RGBA image is bounded by `max_dimension`.

Preview semantics are fixed for v1:

- valid score cells use a red → amber → green ramp for score `0..1`;
- invalid-data cells are transparent;
- hard-excluded cells are dark slate and semi-opaque;
- score resampling is bilinear;
- status resampling is nearest-neighbour.

The preview is a visualization only. The canonical GeoTIFF remains the authoritative numeric artifact.

## Frontend

The existing MapLibre page now has a **Suitability Artifact ID** panel. The value is stored in the URL as `suitability_artifact_id` and can be used independently of `project_id` / `dataset_version_id`.

After metadata loads, the preview is added as a MapLibre `image` raster source below boundary/road/building/water/landuse layers. The UI provides:

- raster visibility toggle;
- opacity slider;
- fit-to-suitability action;
- score legend and hard-mask legend;
- threshold/cell statistics;
- factor version, weight and normalization metadata;
- hard-exclusion provenance.

Example URL:

```text
http://localhost:5173/?suitability_artifact_id=<artifact-uuid>
```

A combined source + suitability view can include all three parameters:

```text
http://localhost:5173/?project_id=<project-uuid>&dataset_version_id=<dataset-version-uuid>&suitability_artifact_id=<artifact-uuid>
```

## Hands-on Ryazan demo

A synthetic, geographically positioned demo is provided only to exercise the UI. It is **not** a real suitability analysis of Ryazan and is intentionally isolated from core algorithms/configuration.

Start the stack, then seed the artifact inside the API container so DB and mounted artifact storage use the same runtime configuration:

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec api uv run python scripts/seed_ryazan_suitability_demo.py
```

The command prints a `Suitability Artifact ID` and a ready-to-open URL such as:

```text
http://localhost:5173/?suitability_artifact_id=<printed-uuid>
```

The synthetic raster is placed over the Ryazan reference area in EPSG:32637. The city reference exists only in the demo script; API contracts, storage contracts, CRS handling and suitability algorithms remain city-agnostic.

## Current boundary

S04-T12 visualizes an existing suitability artifact. A user-friendly workflow that automatically turns arbitrary uploaded source data into a completed suitability artifact is still separate orchestration work; the demo seeder exists so this visualization slice can be verified now without pretending that end-to-end generation already exists.
