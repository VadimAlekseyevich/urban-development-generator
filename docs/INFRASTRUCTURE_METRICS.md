# Infrastructure raw metrics

> **Status: Implemented through UG-AI-036 / S10-T11**

S10-T11 derives the canonical infrastructure `RawMetricId` values from already computed S10
results. The metric layer is pure core logic: it receives no `NetworkBackend`, performs no snap,
path search, geometry generation or persistence query.

## Canonical outputs

`InfrastructureMetricsBuilder` produces exactly these existing benchmark ids, in canonical order:

- `infrastructure.population_coverage_ratio`;
- `infrastructure.age_specific_coverage`;
- `infrastructure.network_distance_p50_m`;
- `infrastructure.network_distance_p90_m`;
- `infrastructure.unmet_demand`;
- `infrastructure.capacity_utilization`.

No second metric-id vocabulary is introduced. `InfrastructureRawMetricValue.metric_id` is the
existing `domain.benchmarking.RawMetricId`.

## Inputs and provenance

The builder consumes:

- T03 `UnmetDemandResult`, including gross/existing-served/unmet demand and existing capacity;
- T07 existing-facility `InfrastructureAccessibilityBatchResult` values;
- final T08 `InfrastructureGreedyPlacementState` values, whose coverage cache already contains
  candidate T07 distances and whose accepted facilities define generated service order;
- canonical `InfrastructureType` values used by placement and T10 persistence.

T10 does not create a second core result model. Its writer persists the same accepted T08
facilities/type capacity/network provenance; metrics therefore reuse the authoritative core
objects instead of importing backend ORM types.

Inputs must align exactly by infrastructure type, demand identity, network snapshot and initial
unmet demand. A provenance mismatch is an error; the metric layer never tries to repair it by
rerunning routing.

## Coverage

For each final demand row the builder compares gross demand with final T08 remaining demand.
The served fraction is projected back to the original T03 `source_signal_value`.

Population coverage aggregates service requirements whose demographic dependency is either
`total_population` or `age_group`. Workforce/jobs demand is intentionally excluded from this
population-named metric.

Age-specific coverage groups only `age_group` demand by stable `demographic_group` code and
returns a canonical distribution of population, covered population and coverage ratio.

When several infrastructure types depend on the same population/cohort, the metric represents
coverage across those service requirements; each type contributes its own population-linked
requirement to numerator and denominator.

## Distance without rerouting

Existing service distance is taken from the T07 nearest reachable existing-facility outcome for
each demand row with positive pre-placement `served_demand`.

Generated service is replayed from the final T08 accepted order and its immutable coverage cache.
The replay performs only capacity accounting: it does not call a routing backend. Each positive
served amount becomes a distance sample using the cached T07 distance.

P50/P90 use served-demand-weighted nearest-rank percentiles over existing and generated service
samples. How an empty sample set or unreachable-only case is represented belongs to UG-AI-037.

## Unmet demand and utilization

Unmet demand is the sum of final T08 `remaining_demand` across infrastructure types.

Capacity utilization is:

`total served demand / (T03 existing capacity + accepted generated facility capacity)`.

Generated capacity is the accepted-facility count multiplied by the authoritative
`InfrastructureType.capacity`, matching T08/T10 semantics. A normal non-empty result must not
serve more than total available capacity.

Zero-capacity, zero-demand and other empty-data behavior is deliberately not guessed in UG-AI-036;
UG-AI-037 owns those policies.

## Bounds and diagnostics

The builder has an explicit infrastructure-type bound and inherits bounded demand/candidate/cache
inputs from T03/T07/T08. Diagnostics record type count, demand count, accepted generated facility
count and existing/generated distance sample counts.

## Explicit non-goals

UG-AI-036 does not:

- define empty/unreachable/zero-denominator output policy;
- persist raw metrics;
- extend the S11 metric registry metadata;
- rerun snapping/routing or recompute candidate accessibility;
- add API/UI.

Ordered follow-up:

- UG-AI-037 — define percentile/unmet/utilization empty-data and unreachable policies;
- UG-AI-038 — add numeric/property tests for bounds, percentiles and capacity conservation.
