# S13-T13 · Complete project workspace UI

**Scope:** UG-AI-093. The React/MapLibre shell now exposes four navigable
sections: **Данные**, **Карта**, **Генерация**, **Анализ**. The map stays mounted
while sidebar sections are hidden; existing specialist map panels keep their
canonical layer registration and visibility. The frontend does not introduce
a second layer registry, Stage model, metrics vocabulary or job lifecycle.

## User journey and authoritative boundaries

1. In **Данные**, list canonical projects via bounded
   `GET /projects?limit=50&offset=N`, select an existing project or create
   one via `POST /projects` with explicit metric `working_srid`.
2. Select a ready, project-owned DatasetVersion from the bounded version
   catalog added in S13-T12; that action updates the shared workspace and
   opens **Карта**. Unready versions cannot be opened. The original raw
   multipart upload feature remains available, but `POST /uploads`
   **only creates a ready Artifact**; it does not create a DatasetVersion.
3. In **Карта**, inspect the existing canonical 18-layer tree with exact
   project/version/run/artifact owner binding and the specialist vector/raster
   panels. When the project changes, owner-qualified panel instances are
   remounted and stale source features are cleared.
4. In **Генерация**, edit mode, seed, config schema/version, full JSON and
   executable commit SHA. Create/cancel/retry and progress go through the
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
