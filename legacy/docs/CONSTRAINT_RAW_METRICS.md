# Constraint raw metrics

> **Status: Implemented through UG-AI-055 / S11-T09**

S11-T09 projects the canonical `ValidationReport` directly into the three
`MetricSource.CONSTRAINTS` raw metrics. It does not introduce a second violation model and does
not rerun any constraint rule.

## Canonical values

- `constraints.hard_violation_count` — the number of `ValidationReport.hard_failures`.
- `constraints.affected_area_m2` — the union area of polygonal `problem_geometry` evidence from
  all failed HARD and SOFT results. Overlap is counted once. Passed-result geometry is ignored.
- `constraints.weighted_soft_penalty` — the sum of the existing
  `SoftPenaltyMetadata.weighted_penalty` values on failed SOFT results.

All three values are emitted in canonical registry order and are non-negative raw values.

## Affected-area policy

`ConstraintProblemGeometry` is already expressed in a metric working CRS. S11-T09 therefore
does not reproject geometry. All geometry-bearing failures must use one `working_srid`; mixed
failure SRIDs are rejected rather than combined.

Only polygonal area contributes. Point/line evidence remains valid validation detail but adds
zero square metres. Invalid problem geometry is allowed by the validation contract because the
invalid shape itself can be violation evidence; before area union, S11-T09 repairs such evidence
with Shapely `make_valid` and records the repair count in diagnostics.

Geometry-free failures remain valid and add zero affected area. Diagnostics separately record
geometry-bearing and missing-geometry failures.

The union is explicitly bounded by `max_problem_geometries` (default 100,000). Reports exceeding
the configured geometry bound fail closed before union work starts.

## Soft-penalty compatibility

Versioned S11-T03 SOFT results contribute `raw_penalty * weight` exactly as stored in
`SoftPenaltyMetadata`. Legacy failed SOFT results with no penalty metadata remain compatible:
they contribute zero to the weighted metric and are counted in `missing_soft_penalty_count`.

Passing SOFT rules never contribute because their canonical raw penalty is zero and they are not
`soft_violations`.

## Non-goals

S11-T09 does not normalize values, define score ranges/clamping/missing policy, persist raw
metrics, expose API/UI payloads, or recompute validation. Those remain later ordered S11 tasks.
