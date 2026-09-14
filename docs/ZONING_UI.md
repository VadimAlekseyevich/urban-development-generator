# Zoning UI vertical slice (S05-T09)

S05-T09 exposes persisted functional zoning through the existing interactive MapLibre map.
It is a read-only vertical slice: source state is not mutated and generated zoning remains scoped
by `run_id`.

## HTTP contract

Two read endpoints are added:

- `GET /api/v1/projects/{project_id}/zoning-runs`
- `GET /api/v1/projects/{project_id}/zoning-runs/{run_id}/zones/geojson?bbox=...&limit=...`

The run list is project-scoped and includes the persisted generated-zone count. The zone endpoint
accepts a WGS84 viewport bbox, uses the run working CRS for the spatial query, and returns GeoJSON
in EPSG:4326. The endpoint is bounded by the same style of explicit feature limit used by source
layer delivery.

## Generated zones

`GeneratedZone` rows from S05-T08 are read directly from PostGIS. The map keeps geometry
interactive and colors the four classes separately:

- `residential`
- `mixed`
- `public`
- `recreation`

Changing the selected run changes only the generated-zone source. Source layers and other runs are
not mutated.

## Fixed zones

S05-T02 deliberately represents existing/fixed zoning as an immutable semantic view over selected
source landuse references rather than introducing another canonical source table. The T09 UI follows
that contract: the **Fixed zones** overlay reads `source_landuse` for the active DatasetVersion using
the existing source-layer bbox API.

This means the application/ingest boundary is responsible for using a landuse dataset that really
represents fixed zoning when that semantic is required. The UI does not infer zoning semantics from
OSM landuse labels and does not rewrite source data.

## UI behavior

The zoning panel provides:

- project-scoped run selection;
- independent visibility for fixed and generated zones;
- independent opacity controls;
- viewport object counts and truncation warnings;
- a four-class generated-zone legend;
- `run_id` persistence in the browser query string after explicit run selection.

Both zoning overlays are real MapLibre GeoJSON sources. They move and zoom with the map and are not
pre-rendered image placeholders.

## Performance boundary

T09 intentionally stays on the current bbox/GeoJSON delivery path. Large-layer MVT delivery, tile
caching and the complete scalable layer registry are roadmap S13 concerns. T09 therefore preserves
strict viewport limits rather than pretending the current delivery path is the final performance
architecture.

## Out of scope

S05-T09 does not implement:

- generation orchestration that creates a zoning run from the browser;
- edits to fixed or generated zone geometry;
- region refinement or constraint evaluation (S05-T06/T07 already own those contracts);
- persistence changes (S05-T08 already owns them);
- vector tiles/MVT or tile caching;
- a city-specific zoning interpretation.
