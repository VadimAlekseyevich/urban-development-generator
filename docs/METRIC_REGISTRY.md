# Canonical raw metric registry

> **Status: Implemented through UG-AI-055 / S11-T09**

The canonical raw metric identity remains `RawMetricId` from
`core.urban_generator.domain.benchmarking`. S11-T04 enriches the existing
`MetricDefinition` contract; it does not introduce a second metric-id vocabulary.

## MetricDefinition metadata

Every canonical definition now carries:

- `metric_id` — one existing `RawMetricId`;
- `unit` — raw value unit;
- `value_kind` — scalar or distribution;
- `scope` — runtime domain grouping (`LAND`, `BUILDINGS`, `ROADS`,
  `DEMOGRAPHY`, `INFRASTRUCTURE`, `CONSTRAINTS`);
- `direction` — `HIGHER_IS_BETTER`, `LOWER_IS_BETTER`, `TARGET`, or
  `DESCRIPTIVE`;
- `source` — canonical producer family used by the S11 raw-metric adapters;
- `version` — stable definition-contract version.

`direction` is descriptive metadata, not normalization logic. Target ranges, clamping,
missing-value policy and normalized values remain owned by S11-T10. Composite weighting remains
owned by S11-T11.

## Canonical registry

`CANONICAL_METRIC_REGISTRY` is the single deterministic registry for the v1 raw metric catalog.

It:

- is keyed only by `RawMetricId`;
- rejects duplicate IDs and malformed metadata;
- preserves the canonical declaration order;
- exposes exact lookup by ID;
- supports deterministic filtering by `MetricScope` and `MetricSource`;
- contains every current `RawMetricId` exactly once.

`CANONICAL_RAW_METRIC_DEFINITIONS` remains the ordered tuple of definitions and
`CANONICAL_RAW_METRIC_IDS` remains the ordered ID tuple for existing benchmark and experiment
contracts. Both are backed by the same canonical registry.

## Source ownership

`MetricSource` identifies the producer family rather than a persistence table or HTTP endpoint:

- `LAND_BUILDING` — S11-T05;
- `ROADS` — S11-T06;
- `DEMOGRAPHY` — S11-T07;
- `INFRASTRUCTURE` — S11-T08;
- `CONSTRAINTS` — S11-T09.

Those adapters must reuse authoritative stage outputs and this registry metadata. They must not
redefine IDs, units, directions, scopes, sources or versions locally.

S11-T05 implements `LAND_BUILDING`, S11-T06 implements `ROADS`, S11-T07 implements
`DEMOGRAPHY`, S11-T08 implements `INFRASTRUCTURE`, and S11-T09 implements `CONSTRAINTS`.
Their exact source/empty-data policies are documented in `LAND_BUILDING_METRICS.md`,
`ROAD_RAW_METRICS.md`, `DEMOGRAPHY_RAW_METRICS.md`,
`INFRASTRUCTURE_RAW_METRICS.md`, and `CONSTRAINT_RAW_METRICS.md`.

## Non-goals

S11-T04 does not compute raw values, normalize metrics, assign composite-score weights, persist
score inputs, or define dashboard payloads. Those remain ordered future tasks.
