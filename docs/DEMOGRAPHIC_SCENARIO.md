# Demographic scenario configuration

S09-T01 introduces the immutable scenario contract used by the demographic pipeline. It
contains assumptions only; it does not allocate residents, calculate capacity, estimate
jobs, or read calibration rasters.

## Population objective

A scenario contains exactly one typed `PopulationTarget`:

- `total_population` — a non-negative integer target population;
- `growth_rate` — a finite fractional change relative to a later supplied baseline,
  greater than `-1.0`.

The schema deliberately does not guess a baseline population. Resolving a growth target
against source or existing population belongs to later demographic orchestration.

## Housing assumptions

The scenario stores:

- `occupancy_ratio` in `0..1`; `vacancy_ratio` is its complement;
- positive `residential_area_per_person_m2`;
- positive `average_household_size`;
- `residential_gfa_share` in `0..1`.

These values are versioned inputs for S09-T02/T03. T01 does not apply them to generated
buildings.

## Age groups and workforce

Age groups are configurable through `AgeGroupShare`. They must:

- start at age 0;
- form one contiguous, non-overlapping partition;
- end with one open-ended group (`max_age=None`);
- use unique stable codes;
- have shares that sum to 1.0 within numeric tolerance.

The recommended v1 partition remains `0–6`, `7–17`, `18–64`, `65+`, but the core
contract does not hard-code those four labels.

`working_population_ratio` is a scenario-level `0..1` assumption. Job/workforce
estimation semantics remain S09-T05.

## Determinism and provenance

`DemographicScenario` canonicalizes age-group order and exposes a stable SHA-256 content
fingerprint. Equivalent scenario content therefore has identical provenance independent of
input age-group ordering.

## Scope boundary

S09-T01 contains no GFA-to-capacity calculation, resident allocation, age-count rounding,
job estimation, aggregation, population-raster sampling, persistence, API, or UI. Those
belong to S09-T02 through S09-T11.
