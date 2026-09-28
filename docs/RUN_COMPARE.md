# S12-T13 — Persisted run comparison

> **Status: Implemented in UG-AI-078; required CI evidence tracked in PR**

## Ownership and request

`POST /api/v1/projects/{project_id}/compare` accepts `{"run_ids": ["UUID", "UUID", ...]}`
with 2–10 **distinct**, project-scoped run IDs. Input order is preserved and the
first ID is the explicit baseline. Foreign-project and nonexistent runs are both
404; nonexistent project is also 404. Duplicate/oversized requests are 422.

Only successful immutable runs with a persisted S11 `evaluation` envelope and a
canonical `validation_json` report can be compared. Missing/nonterminal
material is 409. Different working metric SRIDs in the same comparison are 409.
Malformed persisted evaluation/validation is a data-integrity error (500).
The read path performs a bounded project-filtered DB query, never invokes GIS
stages, score recalculation, metric producers or constraint evaluation. No new
table, migration, metric identity or validation model is introduced.

## Metrics and ranking

The existing validated S11 evaluation decoder reconstructs the canonical
`CompositeScoreResult`; raw values are joined to
`CANONICAL_METRIC_REGISTRY` for stable ID, unit, scope, direction and definition
version. Only the **persisted scalar score-input subset** is compared. Missing
IDs/values remain null; no synthetic zero values or distribution metrics are
fabricated. Rows follow registry order, values follow requested run order.

For a raw metric, `delta_from_baseline = run.raw_value - baseline.raw_value`
when both are present, otherwise null. All values retain their canonical raw
unit. `rank` uses dense-free competition order (ties share rank, the following
rank skips a position) only when canonical direction is `HIGHER_IS_BETTER` or
`LOWER_IS_BETTER`. For TARGET or DESCRIPTIVE metrics and missing values, rank
is null: no target band/direction is invented by comparison.

Per-run `composite_score` and persisted score/normalization provenance are
shown regardless of comparability. `scores_comparable` is true only when all
runs share score config ID/version, normalization profile ID/version, metric-ID
set, normalization policy versions and configured/normalized metric weights.
Only then are score deltas and competition ranks populated; otherwise both
fields are null. Comparison never recalculates scores.

## Validation

Each persisted canonical validation payload is deserialized using
`deserialize_validation_report`, bounded by the existing 10,000-result writer
limit and checked against the run metric SRID. Counts are derived from canonical
`ValidationReport.failures`, `hard_failures`, `soft_violations` and failures
with `problem_geometry`. Passing results are not violations; no geometry
is serialized or transformed for comparison. Other violation details remain
available from the S11 violations API.

## Response and acceptance

The response includes `project_id`, `baseline_run_id`, ordered `run_ids`,
`scores_comparable`, a `runs` array with score/provenance/validation summary
and a `metrics` array with the per-run raw value, absolute delta and rank.
The schema is typed in `backend/app/schemas/run_compare.py`.

`tests/integration/test_run_compare_api.py` covers canonical metric order,
input-order preservation, raw deltas/ranks, incompatible score policy, validation
failure summaries, deterministic replay, read-only successful run state, scope,
cardinality, missing provenance and malformed payloads. Later S12-T15 owns
comparison UI; S13-T09 owns compare CSV export.
