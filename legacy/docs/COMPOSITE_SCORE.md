# Composite score

> **Status: Implemented in UG-AI-057 / S11-T11**

S11-T11 defines composite scoring as a pure transformation over canonical scalar raw metrics,
the S11-T10 normalization profile and an explicit versioned weight configuration. The score is an
explainable derived value; canonical raw inputs remain preserved independently inside the result
and persisted run evaluation envelope.

## Core contract

`CompositeScoreConfig` contains:

- stable `config_id`;
- stable `version`;
- one unique non-negative weight for every metric in the selected
  `MetricNormalizationProfile`.

At least one configured weight must be positive. Configured weights do not have to sum to one:
the scorer divides each configured weight by the total weight and records both the configured and
normalized weight.

`CompositeScoreRawMetric` carries the canonical `RawMetricId` and the scalar raw value used for
scoring. The raw input set and weight set must cover exactly the metric IDs in the normalization
profile. Input tuple order is irrelevant; output order follows the deterministic normalization
profile order.

## Score semantics

For every metric the scorer records:

- original raw value;
- normalized value;
- normalization policy version;
- configured weight;
- normalized weight;
- weighted contribution;
- missing/clamped diagnostics inherited from normalization.

The final score is the sum of contributions and is therefore in the inclusive `[0, 1]` range.

A `PROPAGATE` normalization result may remain missing only when that metric has zero score weight.
A missing normalized value with positive weight fails closed. The scorer never silently drops a
metric and never renormalizes around missing data.

Distribution/descriptive metrics are not score inputs because S11-T10 does not define scalar
normalization policies for them.

## Persistence

`SqlAlchemyRunMetricsWriter` stores the versioned evaluation envelope under
`GenerationRun.metrics_json["evaluation"]`.

The envelope contains:

- evaluation schema version;
- composite score;
- score config ID/version;
- normalization profile ID/version;
- every raw score input;
- every normalized value and normalization-policy version;
- configured/normalized weights and contributions;
- clamp/missing diagnostics.

The writer preserves other existing `metrics_json` sections such as demographic read-model data.
Retry before run completion deterministically replaces only the `evaluation` section. A successful
`GenerationRun` remains immutable and cannot have evaluation metrics replaced.

No new database table or migration is needed: `GenerationRun.metrics_json` is the existing
run-level metrics persistence extension point.

This persisted input/provenance envelope is consumed by the implemented S11-T12 sensitivity
engine so weight perturbations can recalculate scores/rankings without rerunning GIS algorithms or
normalization. S11-T14 now also projects the same envelope into the run-scoped metrics dashboard,
combining persisted score inputs with canonical registry units/scope/direction without recomputation.

## Non-goals

S11-T11 itself does not choose one universal weight profile or rerun raw-metric producers.
S11-T12 performs weight-only sensitivity from the persisted envelope and S11-T14 provides its
read-only score explanation API/UI. Scenario comparison, interactive sensitivity controls and the
formal S15 research experiment remain later ordered tasks.
