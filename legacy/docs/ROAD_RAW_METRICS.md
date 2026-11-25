# Road raw metric adapter

> **Status: Implemented through UG-AI-052 / S11-T06**

S11-T06 projects existing S06 road stage artifacts into the canonical `MetricSource.ROADS`
raw metric family. The adapter does not rebuild the road graph and does not rerun road metrics
or validation.

## Input

`RoadMetricAdapter` consumes one authoritative `RoadStageOutput` and reads only:

- `RoadStageOutput.metrics` (`RoadMetrics`);
- `RoadStageOutput.validation.diagnostics` (`RoadValidationDiagnostics`);
- graph counts/component diagnostics for stale-artifact alignment checks.

The adapter never calls `RoadGraphBuilder`, `RoadMetricsCalculator` or `RoadNetworkValidator`.

## Metric mapping

The adapter emits every `MetricSource.ROADS` definition in canonical registry order:

- `roads.length_density_km_per_km2` → `RoadMetrics.length_density_km_per_km2`;
- `roads.connected_components` → `RoadMetrics.component_count`;
- `roads.average_degree` → `RoadMetrics.mean_degree`;
- `roads.intersection_density_per_km2` → `RoadMetrics.intersection_density_per_km2`;
- `roads.circuity` → `RoadMetrics.edge_weighted_circuity`;
- `roads.dead_end_ratio` → `RoadValidationDiagnostics.dead_end_ratio`.

`roads.circuity` is the only road raw metric allowed to be missing. S06 already defines it as
`None` when no edge has a positive endpoint chord; the S11 adapter preserves that policy instead
of inventing a numeric sentinel. Empty graphs therefore produce zero for all other road metrics
and `None` for circuity.

## Alignment checks

Before projection, the adapter requires node/edge/component counts to agree across the metric,
validation and graph artifacts. It also validates numeric bounds and the relationship between
circuity presence and `circuity_edge_count`. This catches stale or mismatched artifacts without
performing any topology or geometry recomputation.

## Non-goals

S11-T06 does not change S06 road metric formulas, graph construction, validation policy,
normalization, composite scoring, persistence or API payloads.
