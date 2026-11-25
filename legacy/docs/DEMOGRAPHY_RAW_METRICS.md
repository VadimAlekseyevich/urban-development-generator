# Demography raw metric adapter

> **Status: Implemented through UG-AI-053 / S11-T07**

S11-T07 projects the authoritative S09 `DemographyMetricsResult` into the canonical
`MetricSource.DEMOGRAPHY` raw metric family. It reuses already-computed totals and age-group
records and does not rerun allocation, aggregation, calibration, employment estimation or
`DemographyMetricsBuilder`.

## Input

`DemographyMetricAdapter` consumes one `DemographyStageOutput` and reads
`DemographyStageOutput.metrics` plus fixed-demography reference count for diagnostics.

## Metric mapping

The adapter emits every `MetricSource.DEMOGRAPHY` definition in registry order:

- `demography.total_population` → `DemographyMetricsTotals.population`;
- `demography.density_per_km2` → `DemographyMetricsTotals.population_density_per_km2`;
- `demography.age_group_distribution` → the existing tuple of `DemographicAgeMetric` values;
- `demography.jobs_estimate` → `DemographyMetricsTotals.jobs_estimate`.

The age-group distribution is not re-encoded into a second domain model. Codes, age bounds,
resident counts and shares remain the S09 typed records.

## Empty-data policy

S09 requires a canonical non-empty age-group tuple even when total population is zero. In that
case resident counts and shares are zero. S11-T07 preserves that representation. Scalar
population, density and jobs are also zero rather than missing.

## Integrity checks

The adapter verifies block-count, population, jobs and age-group share/resident totals before
projection. These are consistency checks over existing numeric artifacts only; they do not
perform GIS or demographic recomputation.

## Non-goals

S11-T07 does not change S09 formulas or calibration policy and does not introduce normalization,
composite scoring, persistence or API behavior.
