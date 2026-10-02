# API

Базовый префикс: `/api/v1`.

## Реализовано

- `GET /health/live` — liveness;
- `GET /health/ready` — готовность PostgreSQL;
- `GET /projects?limit=50&offset=0` — список проектов;
- `POST /projects` — создание проекта;
- `GET /projects/{project_id}` — получение проекта;
- `PATCH /projects/{project_id}` — частичное обновление проекта;
- `DELETE /projects/{project_id}` — удаление проекта.

## Project contract

Проект хранит явный `working_srid` — положительный EPSG SRID проектной метрической CRS.
`AUTO` и географические CRS вроде EPSG:4326 не являются допустимым persisted working CRS.

Минимальный create payload:

```json
{
  "name": "Demo project",
  "description": null,
  "working_srid": 32637,
  "boundary_metadata": {
    "source_srid": 4326,
    "geometry_type": "MULTIPOLYGON",
    "feature_count": 1
  }
}
```

`boundary_metadata` описывает источник/форму project boundary отдельно от PostGIS geometry.
Само поле boundary в persistence остаётся nullable `MULTIPOLYGON`; его ingest/normalization относится к следующим data tasks.

## Generated buildings viewport API

S08-T13 добавляет read-only delivery persisted зданий конкретного run:

- `GET /projects/{project_id}/building-runs` — generation runs проекта с количеством generated buildings;
- `GET /projects/{project_id}/building-runs/{run_id}/buildings/geojson?bbox=west,south,east,north&limit=1500` — bounded viewport GeoJSON.

`bbox` задаётся в EPSG:4326, а spatial filtering выполняется в `GenerationRun.working_srid`.
Ответ возвращается в EPSG:4326 и содержит typed T12 properties: building/source refs,
zone, archetype, use, floors, footprint area и GFA. `truncated=true` означает, что
viewport достиг limit и клиенту следует приблизить карту или повторить запрос меньшей областью.


## Infrastructure run read API

S10-T12 exposes only persisted, run-scoped infrastructure presentation data:

- `GET /projects/{project_id}/infrastructure-runs`;
- `GET /projects/{project_id}/infrastructure-runs/{run_id}/facilities/geojson?bbox=...&limit=1500&origin=existing|generated`;
- `GET /projects/{project_id}/infrastructure-runs/{run_id}/metrics`;
- `GET /projects/{project_id}/infrastructure-runs/{run_id}/demand/geojson?bbox=...&limit=1500`.

Facility and demand GeoJSON are bounded viewport reads in EPSG:4326 with filtering in the run
working CRS. `metrics` and `demand/geojson` return HTTP 409 when the run exists but the
authoritative S10 UI read-model has not been materialized yet. Reads never rerun snapping,
routing, placement or metric computation.


## Validation violations read API

S11-T13 exposes only persisted canonical `ValidationReport` data:

- `GET /projects/{project_id}/validation-runs?limit=50`;
- `GET /projects/{project_id}/validation-runs/{run_id}/violations?offset=0&limit=200`;
- `GET /projects/{project_id}/validation-runs/{run_id}/violations/geojson?bbox=...&limit=200`.

Run summaries expose hard/soft/spatial failure counts. The detail endpoint includes non-spatial
violations, stable entity refs and optional soft-penalty metadata. GeoJSON returns only canonical
problem geometries transformed from the run working CRS to EPSG:4326 and keeps stable
`violation_index` identity from report order. Reads never rerun validation or repair geometries.
Run lists are capped at 100 and violation/GeoJSON requests at 1,000 items.


## Metrics dashboard read API

S11-T14 exposes the persisted S11-T11 evaluation envelope as a bounded, explainable read model:

- `GET /projects/{project_id}/metric-runs?limit=50`;
- `GET /projects/{project_id}/metric-runs/{run_id}/metrics`.

Run lists are capped at 100 and contain only runs with a persisted `evaluation` section. The
detail response returns the persisted composite score, score/normalization provenance and, for
every score metric, canonical raw value + unit, normalized value, configured/normalized weight,
contribution and missing/clamped diagnostics. Units/scope/direction come from the canonical metric
registry. An existing run without an evaluation envelope returns HTTP 409. Reads never rerun raw
metric producers, normalization, GIS or scoring.


## Persisted scenario comparison API

S12-T13 exposes a bounded, read-only cross-run projection over existing immutable
S11 evaluation/validation data:

- `POST /projects/{project_id}/compare` with JSON body
  `{"run_ids":["<baseline UUID>","<comparison UUID>",...]}` (2–10 unique runs).

Input order is retained; the first run is the baseline. The response contains
registered canonical raw scalar score-input metrics, raw deltas from baseline,
direction-aware ranks (null for TARGET/DESCRIPTIVE/missing), score configuration
provenance and score ranks/deltas only when policies/weights are identical. Each
run includes canonical hard/soft/spatial validation failure counts.

404 means missing project/run within project, 422 invalid request, 409 non-successful
or missing persisted inputs/mismatched metric SRID, and 500 malformed persisted
canonical data. No score/normalization/validation/GIS computation is rerun. The
transport and semantic contract is in `docs/RUN_COMPARE.md`.


## Run control and progress API

S12-T14 adds DB-authoritative run creation, cancellation, manual terminal retry and
stage progress snapshots:

- `POST /projects/{project_id}/runs` — create one queued run/job/outbox from
  full config, seed, mode, ready project-owned dataset versions and commit SHA;
- `GET /projects/{project_id}/runs?limit=50` — bounded newest project runs;
- `GET /projects/{project_id}/runs/{run_id}` — persisted job/stage progress;
- `POST /projects/{project_id}/runs/{run_id}/cancel` — one-way cancellation;
- `POST /projects/{project_id}/runs/{run_id}/retry` — idempotent fresh child of
  a failed/cancelled source, never mutating/reusing its outputs.

Request, state and polling semantics are in `docs/RUN_CONTROL.md`. HTTP never
executes GIS or directly enqueues Redis. A running cancellation remains pending
until the worker's cooperative boundary. 404 = project-scoped missing resource,
409 = invalid persisted lifecycle/inputs, 422 = invalid request bounds.

## Compare UI and map switching

S12-T15 adds a frontend consumer of existing `GET /metric-runs` and
`POST /compare` routes. It displays 2–10 persisted run metric, score and
validation columns with explicit baseline, and switches all generated/validation
map panels to one selected compared run ID. Absent run-layer read models are
shown as empty; no other run is substituted. No new HTTP endpoint.
See `docs/COMPARE_UI.md`.

## LayerCatalog contract (S13-T01)

The typed, owner-qualified, versioned catalog lives in
`backend/app/application/layer_catalog.py` and is specified in
`docs/LAYER_CATALOG.md`. Its 18 definitions reference existing routes
and do not assert data materialization or bypass existing project/run/dataset
authorization. This is an application contract only: S13-T01 does not add a
catalog HTTP endpoint; generalized bbox delivery is S13-T02.

## Generic bounded vector API (S13-T02)

`GET /projects/{project_id}/vector-layers/{layer_id}/geojson`
accepts the exact `dataset_version_id` or `run_id` required by LayerCatalog,
WGS84 `bbox=west,south,east,north`, `limit=1..5000`, optional exclusive
UUID `after`, `presentation_srid=4326`, and `simplify_m=0..100`
in projected metre working CRS. Response is a GeoJSON FeatureCollection
with `schema_version=bounded-vector-v1`, immutable scope/definition metadata,
`truncated` and `next_after`. The 15 physical table-backed entries are
supported; single boundary, raster artifact and integer-indexed canonical
validation report retain specialized APIs. 404 indicates unknown
catalog/foreign owner, 409 absent published derived run read model, 422
invalid scope/viewport/policy. Source and successful-run state is never
mutated. Existing per-layer APIs remain unchanged. See
`docs/BOUNDED_VECTOR_API.md`.

## Bounded binary MVT tiles (S13-T03)

`GET /projects/{project_id}/vector-layers/{layer_id}/tiles/{z}/{x}/{y}.mvt`
requires exactly one canonical `dataset_version_id` or `run_id` scope,
with `z=0..16`, in-range XYZ and `feature_limit=1..1000` (default 500).
Output is `application/vnd.mapbox-vector-tile`: a named canonical MVT layer,
stable source feature UUID string properties, a <=N+1 source candidate
header and explicit truncation flag. SQL uses owner+id filtering,
GiST envelope prefilter and exact intersection before bounded geometry
encoding into EPSG:3857 tiles. Empty tiles are zero bytes, MVT responses
are capped at 1 MiB (413) with a transaction-local 5s statement timeout.
Unknown/foreign owners: 404; unpublished derived run models: 409;
invalid bounds/owner/canonical non-tabular layer: 422. Since S13-T04,
ready dataset-version and succeeded-run tiles receive a strong SHA-256
`ETag` and `Cache-Control: private, max-age=31536000, immutable`; an
exact/weak matching `If-None-Match` (or `*`) receives 304 without a body.
Active/unpublished inputs get `private, no-store` and no ETag. Run-existing
facilities additionally require every linked source version to be ready.
The ready lifecycle, published project CRS and dataset project identity are
protected by migration `0023_published_tile_inputs`. See `docs/MVT_TILE_API.md`.

## Asynchronous vector export APIs (S13-T07/T08)

Single-layer GeoJSON:

- `POST /projects/{project_id}/exports/geojson`;
- `GET /projects/{project_id}/exports/geojson/{job_id}`;
- `GET /projects/{project_id}/exports/geojson/{job_id}/download`.

Multi-layer GeoPackage:

- `POST /projects/{project_id}/exports/geopackage`;
- `GET /projects/{project_id}/exports/geopackage/{job_id}`;
- `GET /projects/{project_id}/exports/geopackage/{job_id}/download`.

Both APIs persist a DB-authoritative job/outbox request and return HTTP 202;
worker execution reuses the canonical 15 table-backed LayerCatalog/vector
read models over immutable ready DatasetVersion/succeeded-run owners. GeoJSON
exports exactly one layer with a 100,000 feature cap. GeoPackage accepts 1–15
unique layers in canonical order, supports source+run selection in one project,
caps each layer at 100,000 and the whole artifact at 500,000, and writes
EPSG:4326 geometry plus complete deterministic `properties_json` attributes.
Exceeding a cap fails rather than truncating. Successful artifacts are
referenced with `owner_type=job` and downloads stream through ArtifactStore.
See `docs/GEOJSON_EXPORT.md` and `docs/GEOPACKAGE_EXPORT.md`.

## Canonical raw-metrics CSV export (S13-T09)

- `POST /projects/{project_id}/exports/metrics.csv` with JSON body
  `{"run_ids":["<run UUID>", ...]}` for 1–10 unique project-scoped runs.

The response is a synchronous UTF-8 CSV download because the payload is bounded by the canonical
metric registry and ten runs. Only immutable successful runs are accepted. Values come from the
persisted S11 evaluation envelope; no metric producer, normalization, score, validation or GIS work
is rerun. Rows use canonical registry order and requested run order, include canonical unit/scope/
direction/source/value-kind/version metadata, and distinguish an absent metric from a present metric
with a blank raw value. 404 = project/run scope failure, 409 = non-successful run or missing
evaluation, 422 = invalid run selection, 500 = malformed persisted canonical evaluation.
See `docs/METRICS_CSV_EXPORT.md`.

## Далее

- `/projects/{id}/datasets` — загрузка, импорт и валидация;
- `/projects/{id}/layers` — нормализованные слои;
- `/runs/{id}/metrics` — показатели;

Тяжёлые GIS-операции выполняются в worker: API только создаёт job и возвращает идентификатор запуска.
