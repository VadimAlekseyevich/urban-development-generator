# Metrics dashboard

> **Status: Implemented in UG-AI-060 / S11-T14**

S11-T14 delivers a read-only explanation of the persisted S11 composite score. The dashboard is a
presentation projection over the existing `GenerationRun.metrics_json["evaluation"]` envelope and
the canonical `CANONICAL_METRIC_REGISTRY`; it does not create another metric vocabulary and does
not rerun GIS, raw-metric adapters, normalization, validation or scoring.

## Read contract

The backend exposes project/run-scoped persisted evaluation data:

- `GET /api/v1/projects/{project_id}/metric-runs?limit=50`;
- `GET /api/v1/projects/{project_id}/metric-runs/{run_id}/metrics`.

Run lists contain only runs that have an `evaluation` envelope and are capped at 100 items. The
detail endpoint distinguishes a missing run (404) from an existing run whose evaluation has not
been materialized (409). Unsupported or malformed persisted evaluation schemas fail closed rather
than being partially displayed.

The application service reconstructs the existing `CompositeScoreResult` types from the persisted
payload. This reuses their numeric and conservation invariants: normalized weights sum to one and
metric contributions sum to the persisted composite score.

## Metric explanation

For every score metric the response combines persisted evaluation values with canonical registry
metadata and exposes:

- canonical `RawMetricId`;
- raw value and canonical unit;
- metric scope, direction and registry version;
- normalized value and normalization-policy version;
- configured weight and normalized weight;
- score contribution;
- explicit missing/clamped diagnostics.

Units, scope and direction are resolved from `CANONICAL_METRIC_REGISTRY`, not copied into the
persisted score envelope. This keeps metric metadata under one owner while preserving the exact raw,
normalized and contribution values that were used when the run was scored.

## Frontend

The React dashboard:

- explicitly selects a persisted metric run and keeps the selection in `metrics_run_id` in the URL;
- shows composite score/config/normalization provenance;
- shows raw value + unit, normalized value, weight and contribution for every score metric;
- displays normalization direction/policy version and missing/clamped flags;
- surfaces loading, empty, error and run-list truncation states.

The contribution bar is only a visual projection of the persisted numeric contribution. The
browser does not recompute score arithmetic.

## Persistence and migration

No schema change is required. S11-T11 already persists the versioned evaluation envelope under
`GenerationRun.metrics_json["evaluation"]`, and successful run immutability continues to protect
it. S11-T14 adds only bounded reads and presentation.

## Non-goals

S11-T14 does not implement scenario comparison, score-sensitivity controls, metric exports,
universal normalization ranges or new score configurations. Integrated expected-range/invariant
regression fixtures remain the ordered **UG-AI-061 / S11-T15** task.
