# Age-group allocation

S09-T04 partitions the integer residents produced by S09-T03 into the configurable
`DemographicScenario.age_groups` while preserving exact integer totals.

## Two exact margins

Independent rounding inside every building can drift away from the scenario-wide cohort
shares. T04 therefore solves two margins explicitly:

1. actual allocated generated population is apportioned into integer cohort totals;
2. those cohort totals are distributed across buildings while preserving each building's
   exact resident count.

The output matrix therefore satisfies both:

```text
sum(age groups in one building) = building residents
sum(one age group across buildings) = cohort total
```

## Cohort totals

Scenario shares are interpreted as decimal weights and normalized by their configured
sum. Hamilton/largest-remainder apportionment converts them into integer cohort totals.
Ties follow canonical age-group order from S09-T01.

## Building distribution

Buildings are processed in stable `building_id` order. For each building, its row total
is apportioned against the remaining cohort columns using exact integer
quotient/remainder arithmetic. Remaining ties follow canonical cohort order.

This produces deterministic results without random sampling and without accumulating
rounding error across buildings.

## Zero population

A zero-resident allocation remains valid. Every cohort count and achieved share is zero,
so no division-by-zero, NaN or Inf is emitted.

## Scope boundary

T04 describes generated residents only. It does not estimate jobs/workforce, aggregate by
block/zone, calibrate from population rasters, persist results, or expose API/UI. Job and
workforce estimation remains S09-T05.
