# S11 regression fixtures

> **Status: Implemented in UG-AI-061 / S11-T15**

S11-T15 closes the S11 quality gate with deterministic regression acceptance over the existing
canonical fixtures. It adds no new production algorithm, metric vocabulary, persistence schema or
execution path.

The regression boundary deliberately reuses:

- the M0 in-memory S04-S09 Stage spine;
- the S10 synthetic-town infrastructure end-to-end fixture;
- S11 canonical raw metric adapters;
- canonical aggregate HARD/SOFT validation contracts;
- versioned normalization and composite scoring.

## Canonical metric ID coverage

The five S11 metric adapter families must partition `CANONICAL_RAW_METRIC_IDS` exactly, in
canonical registry order and without duplicates:

1. land/building;
2. demography;
3. roads;
4. infrastructure;
5. constraints.

A new `RawMetricId` therefore cannot silently appear outside the S11 regression coverage. Adding
one requires updating its canonical producer family and expected fixture behavior.

## In-memory scenario envelope

The existing deterministic 10 m × 10 m, EPSG:32637 reference spine is projected through the S11
land/building, road and demography adapters.

Accepted land/building envelope:

- developable area: exactly 100 m²;
- developed area: 90–100 m²;
- green/recreation share: 0;
- building coverage ratio: 0.10–0.22;
- FAR: 0.20–0.45;
- total generated GFA: 20–45 m²;
- generated archetype shares sum to 1 and the reference fixture remains 100% POINT archetype.

Accepted road envelope:

- connected components: exactly 1;
- length density: 300–1,500 km/km²;
- average degree: 1.5–2.5;
- intersection density: 0–30,000 /km²;
- circuity: 1.0–1.2;
- dead-end ratio: 0–0.4.

Accepted demography envelope:

- generated population: exactly 8;
- density: 50,000–120,000 persons/km²;
- jobs estimate: 0 for the residential-only fixture;
- age-group resident counts conserve the population total and shares sum to 1.

These are regression bounds around the synthetic acceptance scenario, not universal planning
targets.

## Final validation regression

The authoritative coverage/FAR/density values from the same fixture are passed into the canonical
aggregate validation contracts. Capacity utilization is fixed to 1.0 here and is independently
validated by the S10 infrastructure fixture below.

The reference final-validation profile is intentionally:

- inside all HARD bounds, so hard violation count remains 0;
- outside one explicit SOFT coverage preference, so weighted soft penalty remains 0.5;
- non-spatial at this aggregate layer, so affected problem area remains 0 m².

This proves that a valid scenario can still expose a non-blocking quality preference through the
same `ValidationReport` used by the violations/API layer.

## Score regression

The same persisted-style raw inputs are passed through a test-specific, versioned normalization
profile and equal weights:

- building coverage;
- FAR;
- population density;
- hard violation count;
- weighted soft penalty.

The first four normalize to 1.0 in the reference envelope and the soft penalty normalizes to 0.75,
so the explainable composite score is exactly **0.95**. The regression also asserts that metric
contributions sum back to the score.

The normalization profile exists only for this regression fixture. S11-T15 does not introduce
global planning ranges or a production default score configuration.

## Infrastructure regression

The existing S10 synthetic town remains the infrastructure reference and is additionally required
to pass through `InfrastructureMetricAdapter` in canonical registry order.

Accepted invariants:

- population coverage ratio: 0.70–0.80;
- child-specific coverage: 0.75;
- final unmet demand: 5 demand units;
- capacity utilization: 1.0;
- p50 network distance: 100 m;
- p90 network distance: 100 m;
- `0 <= p50 <= p90 <= 250 m`, matching the configured service threshold;
- deterministic generated facilities and fixed-source immutability remain covered by the existing
  S10 acceptance tests.

## CI evidence

Regression-code evidence before documentation closure:

- commit: `9b21116ee5cb05737afec956829617e8775bf06e`;
- pull-request CI run: `36125165479` / CI #566;
- Python lint, mypy, full pytest, road/block/building/infrastructure reference benchmarks and
  migration smoke: green;
- frontend typecheck/build: green;
- complete Docker Compose smoke/readiness: green.

The final PR/main HEAD must remain green after documentation closure.

## Non-goals

S11-T15 does not implement the S12 executable DAG, worker orchestration, checkpoints, scenario
batches or compare workflow. It also does not replace the larger S14 performance benchmark suite
or the real-territory S15 research experiments.
