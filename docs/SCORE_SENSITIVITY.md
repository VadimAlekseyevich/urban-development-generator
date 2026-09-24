# Score sensitivity

> **Status: Implemented in UG-AI-058 / S11-T12**

S11-T12 recalculates composite-score rankings under explicit weight perturbations by reading the
persisted S11-T11 evaluation envelope. It never reruns GIS algorithms, raw-metric adapters,
normalization, routing, placement, or validation.

## Input boundary

The analysis consumes persisted run evaluation snapshots containing:

- canonical `RawMetricId`;
- raw scalar value;
- persisted normalized value;
- normalization policy version;
- normalization profile ID/version;
- clamp/missing diagnostics;
- baseline score config and configured weights.

The database reader loads only `GenerationRun.metrics_json["evaluation"]`. It does not query
generated geometries or invoke core GIS services.

Requests are bounded by `max_runs` (default 100), preserve the caller's requested run order at the
reader boundary, and fail if any requested run is missing or lacks a supported evaluation envelope.

## Compatibility rules

Runs may be compared only when they share:

- the same normalization profile ID/version;
- the same metric ID set;
- the same per-metric normalization policy versions;
- the same persisted baseline score config and configured weights.

These checks prevent a weight-sensitivity result from silently mixing incomparable normalization
semantics.

## Perturbation semantics

`ScoreWeightPerturbation` supplies one non-negative multiplier for every metric in the baseline
score config. The multiplier is applied to the persisted configured weight, after which the normal
composite-score weight normalization is repeated.

At least one effective weight must remain positive.

Because this task is **weight sensitivity**, normalization is deliberately not recomputed. Persisted
normalized values are reused exactly. A persisted missing normalized value may participate only
while its effective score weight is zero.

A metric whose baseline configured weight is zero remains zero under multiplicative perturbation;
activating previously excluded metrics is a different score configuration, not a perturbation of
that baseline.

## Ranking

For every perturbation the engine returns:

- perturbed configured weights;
- recalculated score for every run;
- deterministic rank;
- baseline score/rank;
- score delta;
- rank delta.

Higher score ranks first. Exact score ties use stable `run_ref` ordering so results are reproducible
under input permutation.

## Non-goals

S11-T12 does not modify persisted runs, create new GIS outputs, change normalization policy,
provide API/UI, or run the S15 research experiment matrix. S15-T09 will use this capability for the
formal thesis sensitivity experiment.
