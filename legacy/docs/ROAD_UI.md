# Roads UI vertical slice (S06-T16)

S06-T16 exposes persisted generated roads next to canonical source roads without copying or mutating source data.

## Read API

- `GET /api/v1/projects/{project_id}/road-runs` lists generation runs and persisted generated-road edge counts.
- `GET /api/v1/projects/{project_id}/road-runs/{run_id}/roads/geojson?bbox=west,south,east,north&limit=N` returns bounded generated-road GeoJSON in EPSG:4326.
- `GET /api/v1/projects/{project_id}/road-runs/{run_id}/diagnostics` returns run-wide diagnostics derived from persisted generated edge provenance.
- Existing/fixed roads continue to use the canonical source-layer endpoint: `.../source-layers/roads/geojson`.

The generated-road feature properties expose the persistence contract from S06-T15, including `road_class`, `origin`, edge/road identifiers, node identifiers, length, classification reason/strategy, and source-road references.

## Graph diagnostics

Diagnostics are deterministic over the persisted generated edges of the selected run:

- edge, logical-road and endpoint-node counts;
- connected component count from persisted endpoint identifiers;
- dead-end node count and ratio;
- total generated length;
- counts grouped by road class and generation origin.

These diagnostics describe the persisted generated delta. Canonical existing roads remain in `SourceRoad` and are shown separately by the UI.

## UI behaviour

The roads panel uses separate MapLibre sources/layers for existing and generated roads. Generated roads are styled by `arterial`, `collector`, and `local` class, while existing roads use a neutral source-network style. Both layers are bounded to the active viewport and report truncation when the configured limit is reached.
