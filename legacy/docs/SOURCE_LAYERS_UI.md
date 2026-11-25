# Source layers UI vertical slice (S03-T14)

The frontend now exposes a map-first inspection slice for normalized source data.
It is intentionally read-only: ingestion and semantic mapping stay in backend/worker services,
while the browser requests bounded GeoJSON for the current viewport.

## Context

Open the frontend with a project and dataset-version context either through the sidebar form or URL query parameters:

```text
?project_id=<uuid>&dataset_version_id=<uuid>
```

The context is persisted in the URL so the same normalized version can be reopened directly.

## Visible layers

The map renders and toggles:

- project boundary;
- roads;
- buildings;
- water;
- landuse.

The project boundary is read from `Project.boundary` through
`GET /api/v1/projects/{project_id}/boundary/geojson`. It is transformed from the project working CRS to EPSG:4326 and is used for the initial fit when present. A project without a boundary returns a valid GeoJSON Feature with `geometry: null`.

Dataset-version source layers use the S03-T13 viewport endpoint:

```text
GET /api/v1/projects/{project_id}/dataset-versions/{dataset_version_id}/source-layers/{layer}/geojson?bbox=west,south,east,north&limit=1500
```

Viewport requests remain bounded. The UI shows `count+` and a warning when the API reports `truncated=true`; zooming in narrows the query instead of requesting an unbounded export.

## Interaction

- Pan/zoom triggers a fresh viewport query for currently visible source layers.
- Layer checkboxes control MapLibre visibility and request only enabled dataset layers.
- `Fit to data` prefers the project boundary and otherwise fits the currently loaded visible features.
- Clicking a rendered feature opens an inspector with canonical columns and preserved source attributes.
- `Refresh` explicitly reloads the current viewport.

## Boundary API contract

The boundary helper exists only to support the map vertical slice. It is a read-only adapter over the existing `Project.boundary` column; no schema migration is introduced. Spatial transformation remains in the PostGIS repository adapter rather than the FastAPI controller or React code.

## Acceptance check

With a project whose boundary is populated and a ready DatasetVersion containing canonical source rows:

1. enter the two UUIDs and open the data;
2. the map fits to the project boundary;
3. roads/buildings/water/landuse appear according to the current viewport;
4. toggling a legend row changes visibility;
5. panning or zooming refreshes bounded data;
6. clicking a feature shows its canonical/source properties;
7. truncated layers show a warning instead of silently pretending the viewport is complete.
