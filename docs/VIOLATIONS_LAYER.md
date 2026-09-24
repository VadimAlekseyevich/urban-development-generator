# Run-scoped violations layer

> **Status: Implemented in UG-AI-059 / S11-T13**

S11-T13 delivers the canonical `ValidationReport` to backend/API/frontend without introducing a
second violation domain model. Persistence stores the existing versioned validation codec payload;
the read path deserializes it back into `ValidationReport` before projecting failures into HTTP
schemas or GeoJSON.

## Persistence boundary

Migration `0019_run_validation` adds nullable `GenerationRun.validation_json`.

`SqlAlchemyRunValidationWriter` accepts only a canonical `ValidationReport` and stores the
JSON object produced by `serialize_validation_report()`. It does not persist a parallel
`Violation` entity/table.

The writer enforces:

- at most 10,000 validation results per persisted report;
- every `problem_geometry.working_srid` matches the run's snapshot `working_srid`;
- retry before completion replaces the report deterministically;
- a successful `GenerationRun` cannot have its validation report replaced.

The existing PostgreSQL successful-run immutability trigger covers the new column because it
guards the complete run row. The ORM immutable-field set also includes `validation_json`.

This is the persistence handoff intended for the future S12 `final_validation` stage. S11-T13
does not implement the production DAG/worker.

## Read API

All reads are project/run scoped and use persisted data only:

- `GET /api/v1/projects/{project_id}/validation-runs?limit=50`;
- `GET /api/v1/projects/{project_id}/validation-runs/{run_id}/violations?offset=0&limit=200`;
- `GET /api/v1/projects/{project_id}/validation-runs/{run_id}/violations/geojson?bbox=...&limit=200`.

The run list exposes hard/soft/spatial violation counts. The detail endpoint returns all failed
canonical results, including non-spatial violations and optional soft-penalty metadata. Passed
results remain persisted in the full report but are not projected as violations.

The GeoJSON endpoint returns only failed results with `problem_geometry`. Features expose:

- deterministic `violation_index` from canonical report order;
- `code`;
- `severity`;
- `scope`;
- `message`;
- optional stable `entity_id`;
- optional flattened soft-penalty values.

Problem geometries are transformed from the run working CRS to EPSG:4326. Bbox filtering uses the
transformed geometry envelope rather than geometry repair/exact intersection because an invalid
geometry can itself be the canonical validation evidence and must not be mutated merely for UI
delivery.

Run lists are bounded to 100 records per request; violation detail/GeoJSON requests are bounded to
1,000 items. The persisted report itself is bounded to 10,000 results.

## Frontend

`ViolationsPanel` uses the typed `validationApi.ts` client. It provides:

- validation-run selection persisted in `validation_run_id` URL state;
- hard/soft/spatial counters;
- a bounded violation list including non-spatial failures;
- a toggleable MapLibre problem-geometry layer;
- severity styling (HARD/SOFT);
- click inspection of `code`, `message`, `scope`, and `entity_id`;
- explicit truncation/error states.

The frontend is presentation-only. It never decides whether a constraint passed, never repairs
problem geometry, and never recalculates validation.

## Non-goals

S11-T13 does not implement the metrics dashboard (S11-T14), final S11 regression fixture
(S11-T15), production orchestration/final-validation execution (S12), MVT generalization (S13), or
the research experiment package (S15).
