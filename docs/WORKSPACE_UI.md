# S13-T13 · Complete project workspace UI

> Workspace shell and implemented controls; successful real generation is still blocked by #202. See [WORKING_APPLICATION.md](WORKING_APPLICATION.md).

**Scope:** UG-AI-093. The React/MapLibre shell now exposes four navigable
sections: **Данные**, **Карта**, **Генерация**, **Анализ**. The map stays mounted
while sidebar sections are hidden; existing specialist map panels keep their
canonical layer registration and visibility. The frontend does not introduce
a second layer registry, Stage model, metrics vocabulary or job lifecycle.

## User journey and authoritative boundaries

1. In **Данные**, list canonical projects via bounded
   `GET /projects?limit=50&offset=N`, select an existing project or create
   one via `POST /projects` with explicit metric `working_srid`.
2. Set the project boundary with the GeoJSON upload panel, then upload
   source data and submit an ingest job as described in [INGEST_SUBMISSION.md](INGEST_SUBMISSION.md).
   Select a ready, project-owned DatasetVersion from the bounded version
   catalog added in S13-T12; that action updates the shared workspace and
   opens **Карта**. Unready versions cannot be opened. The original raw
   multipart upload feature remains available, but `POST /uploads`
   **only creates a ready Artifact**; it does not create a DatasetVersion.
3. In **Карта**, inspect the existing canonical 18-layer tree with exact
   project/version/run/artifact owner binding and the specialist vector/raster
   panels. When the project changes, owner-qualified panel instances are
   remounted and stale source features are cleared.
4. In **Генерация**, choose 1–32 ready dataset versions from the project catalog
   (20 per page). Selections persist across pages and are independent of the
   map's inspected version; a project change clears them. Choose mode and seed,
   import a complete v5 JSON file (up to 1 MiB) or paste/edit its content, and
   specify the executable commit SHA. The initial neighbourhood profile can
   instead build the same v5 JSON from bounded numeric fields and a suitability
   source. Explicitly click «Сформировать конфигурацию» to replace the JSON;
   preview does not queue work. It is a pilot profile, not a planning standard
   or a validated city-wide default. Create/cancel/retry and progress go through the
   existing DB/outbox-authoritative S12 run endpoints; successful runs can
   be explicitly pinned to the map without first comparing them.
5. In **Анализ**, use the persisted metrics dashboard and 2–10 run comparison.
   All generated and validation map layers share **one** `mapRunId` pin.
   No GIS reruns or invented metrics are triggered by analysis screens.

Project, DatasetVersion and map run are represented by a single
`WorkspaceSelection`, mirrored in URL `project_id`, `dataset_version_id`,
and `map_run_id`. The sidebar section is `workspace_view`. A project switch
clears its previous dataset/run plus metrics and suitability URL refs;
changing DatasetVersion clears the run pin. A ready version selection is
scoped to the current project by the backend. A manually supplied UUID is
still subject to backend owner validation, not treated as authorization.
A run selected in the comparison or metrics panel changes the same pin;
an absent layer never falls back to another run.

## Limits and future work

S13-T13 composes **existing** API capabilities and preserves their limits:
the project list is paged by 50, the DatasetVersion list by 20, existing run
and metrics lists are bounded, and source viewport reads are capped.
Tabs hide rather than unmount panels so MapLibre layer side effects and
current form state remain stable; project changes remount map-specific
panels to invalidate previous owner state.

This work does **not** invent an endpoint for linking arbitrary uploaded
Artifacts to new DatasetVersions, ingestion jobs, or a browser-side upload
manifest. Those operations need their authoritative persisted workflow.
The Playwright create/upload/run/inspect/compare/export acceptance fixture
remains the separately ordered S13-T14 task; it must surface any missing
version-ingest API rather than fabricate server state.

## Regression gates

Frontend Node tests assert URL restoration, project/version owner resets,
project API read/create/error flows, and a single root workspace/pin. Existing
layer registry/tree, compare and polling tests remain mandatory, together
with Python/core, PostGIS, benchmark, migration and Compose CI.

## Project boundary input (P0)

The **Данные** workspace includes a **Граница территории** panel. Select or
create a project, then upload a GeoJSON Polygon/MultiPolygon (geometry, Feature
or single-feature FeatureCollection) in WGS84 longitude/latitude, up to 2 MiB.
The panel submits the existing project boundary PUT endpoint and refreshes the
canonical boundary map source after a successful server response. The backend
owns topology, CRS and post-run immutability checks; HTTP errors are shown in
the panel. Drawing/editing on the map is not included in this slice.

The real browser ingest test now also exercises boundary persistence and the
subsequent canonical GET before importing the source roads. This does not
resolve the production generation worker blocker (#202).


### Newly completed runs

Pinning a run refreshes generated layer catalogs and the metric catalog,
so results created after the workspace was opened can be discovered without
reloading the application. The planning preset opts into deterministic
spatially balanced eligible road anchors (bounded to 128 picks and 250,000
sampled cells) and parcel-or-block placement; mixed/public blocks without
residential parcels can receive their eligible archetype. Existing JSON
omitting the anchor flag retains score-ranked sampling. These are model
choices, not standards; the pilot browser profile acceptance is still pending.
