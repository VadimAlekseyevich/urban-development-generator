# Metric normalization

> **Status: Implemented in UG-AI-056 / S11-T10**

S11-T10 defines the pure, versioned normalization contract used between canonical raw metrics and
later composite scoring. It does not introduce another metric vocabulary: every policy is keyed by
an existing `RawMetricId`, and its direction must match `CANONICAL_METRIC_REGISTRY`.

## Policy contract

`MetricNormalizationPolicy` is immutable and explicit about:

- canonical `metric_id`;
- canonical `MetricDirection`;
- inclusive raw `lower_bound` / `upper_bound`;
- `NormalizationClampPolicy`;
- `NormalizationMissingPolicy`;
- stable policy `version`;
- `target_lower_bound` / `target_upper_bound` for `TARGET` metrics.

Policies are valid only for scalar, non-descriptive metrics. Distribution metrics and descriptive
metrics remain raw/reporting values and are not silently projected into a score.

## Direction semantics

All present normalized values are in the inclusive `[0, 1]` range.

- `HIGHER_IS_BETTER` is linear from 0 at the lower bound to 1 at the upper bound.
- `LOWER_IS_BETTER` is linear from 1 at the lower bound to 0 at the upper bound.
- `TARGET` uses an explicit best-value band. Values inside the target band normalize to 1; values
  on either side decline linearly to 0 at the outer normalization bounds.

A `TARGET` band may collapse to one target point or touch an outer bound. The outer bounds must
still be strictly ordered.

## Clamp policy

- `CLAMP` bounds an out-of-range present raw value to the configured outer range before
  normalization. `NormalizedMetricValue.was_clamped` records that fact while preserving the
  original raw value.
- `REJECT` fails closed on an out-of-range value.

No extrapolated normalized values outside `[0, 1]` are produced.

## Missing-value policy

Missing means the raw scalar is `None`; it is not inferred from zero or another numeric sentinel.

- `REJECT` fails closed.
- `PROPAGATE` preserves missingness and returns `normalized_value=None`.
- `AS_WORST` preserves `raw_value=None` but emits normalized 0.0.

Road circuity and infrastructure p50/p90 distance can legitimately be missing in their upstream
raw-metric contracts, so score configuration must choose their missing policy explicitly rather
than inventing a sentinel.

## Versioned profiles

`MetricNormalizationProfile` is an immutable ordered set of unique per-metric policies with its
own `profile_id` and `version`. It supports deterministic lookup and a pure
`profile.normalize(metric_id, raw_value)` transformation.

S11-T10 deliberately does **not** hard-code one global numeric range profile. Target/range choices
for population, density, FAR and similar metrics are scenario/research configuration, not universal
properties of the raw metric ID. A later scoring configuration must therefore reference/persist the
exact normalization profile it uses.

## Validation

The contract rejects:

- unknown/untyped metric IDs, directions and policy enums;
- direction mismatch with the canonical metric registry;
- descriptive/distribution normalization policies;
- non-finite bounds or raw values;
- non-increasing outer ranges;
- malformed or out-of-range target bands;
- malformed versions;
- duplicate metric IDs inside a profile.

## Non-goals

S11-T10 itself does not assign weights or aggregate a score. S11-T11 now consumes this contract
through `CompositeScoreConfig` and persists raw score inputs plus normalization provenance in the
run evaluation envelope. Sensitivity analysis and API/UI payloads remain later ordered tasks.
