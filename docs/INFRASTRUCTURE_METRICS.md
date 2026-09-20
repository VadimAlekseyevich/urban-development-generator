# Infrastructure raw metrics

> **Status: S10-T11 complete through UG-AI-038**

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
samples. If there are no positive served-demand samples with a reachable T07 distance, both
percentiles are explicitly unavailable: `InfrastructureRawMetricValue.scalar_value = None`.
The metric layer never converts an unreachable outcome into distance `0` or infinity.

## Unmet demand and utilization

Unmet demand is the sum of final T08 `remaining_demand` across infrastructure types.

Capacity utilization is:

`total served demand / (T03 existing capacity + accepted generated facility capacity)`.

Generated capacity is the accepted-facility count multiplied by the authoritative
`InfrastructureType.capacity`, matching T08/T10 semantics. A normal non-empty result must not
serve more than total available capacity.

Empty-data policy is explicit:

- empty infrastructure/type input yields population coverage `0`, empty age coverage, unmet
  demand `0`, capacity utilization `0`, and unavailable distance percentiles;
- a zero-capacity system with zero served demand has utilization `0`;
- served demand with zero total capacity remains an invalid conservation state;
- unreachable-only demand remains fully unmet unless another authoritative served assignment exists;
- zero-population age cohorts are retained with coverage ratio `0`, while a project with no
  age-linked demand returns an empty age distribution.

## Bounds and diagnostics

The builder has an explicit infrastructure-type bound and inherits bounded demand/candidate/cache
inputs from T03/T07/T08. Diagnostics record type count, demand count, accepted generated facility
count and existing/generated distance sample counts.

UG-AI-038 adds numeric/property hardening over the full metric contract:

- population and age-specific coverage remain finite inside `0..1`;
- zero-population cohorts remain finite with coverage `0`;
- served-demand-weighted p50/p90 are permutation invariant and satisfy `p50 <= p90`;
- positive served demand requires positive total existing/generated capacity;
- capacity utilization remains finite inside `0..1`.

These tests do not rerun routing and exercise only already materialized distance/service inputs.

## Explicit non-goals

UG-AI-038 does not:

- persist raw metrics;
- extend the S11 metric registry metadata;
- rerun snapping/routing or recompute candidate accessibility;
- add API/UI.

Ordered follow-up:

- UG-AI-039 / S10-T12 — add the run-scoped infrastructure read service/repository API.
