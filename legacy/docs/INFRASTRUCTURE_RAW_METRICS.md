# Infrastructure raw metric adapter

> **Status: Implemented through UG-AI-054 / S11-T08**

S11-T08 reuses the existing S10 `InfrastructureMetricsResult` directly as the canonical
`MetricSource.INFRASTRUCTURE` raw-metric output. S10 already emits canonical `RawMetricId`
values, so introducing another value/result model here would duplicate the metric vocabulary.

## Input/output contract

`InfrastructureMetricAdapter.adapt()` accepts one `InfrastructureMetricsResult`, validates that
its metric IDs exactly match the canonical registry-owned infrastructure IDs in registry order,
and returns the same object by identity.

This preserves all existing S10 semantics without translation:

- population coverage ratio;
- typed age-specific coverage;
- p50/p90 network distance;
- unmet demand;
- capacity utilization;
- S10 audit diagnostics.

## Missing-distance policy

S10 represents p50/p90 distance as `None` when there are no served distance samples. S11-T08
preserves those `None` values and does not introduce numeric sentinels.

## No recomputation

The adapter never invokes `InfrastructureMetricsBuilder`, routing, accessibility, demand replay
or greedy placement. Unit tests patch `InfrastructureMetricsBuilder.build` to fail and verify
that adaptation still succeeds.

## Non-goals

S11-T08 does not change S10 infrastructure formulas or empty-data policy and does not add
normalization, scoring, persistence or API behavior.
