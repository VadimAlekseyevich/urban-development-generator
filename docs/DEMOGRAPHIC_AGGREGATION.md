# Block and zone demographic aggregation

S09-T06 joins the generated demographic outputs from S09-T03 through S09-T05 with one
explicit spatial ownership record per building and produces deterministic block and zone
totals.

## Required join

Every generated building must occur exactly once in all four inputs:

- `BuildingAggregationRef` with `block_id`, `zone_id`, and `ZoneClass`;
- S09-T03 population allocation;
- S09-T04 age-group allocation;
- S09-T05 job estimate.

Missing or extra IDs are rejected. Scenario version/fingerprint must also match across the
three demographic result objects.

## Aggregates

For every block and zone T06 sums:

- generated population;
- every configured age-group count;
- approximate jobs.

Blocks must map to exactly one zone and a `zone_id` must map to exactly one
`ZoneClass`. Output rows are sorted by stable IDs.

## Consistency checks

T06 verifies both aggregation levels against the authoritative upstream totals:

```text
sum(block population) = sum(zone population) = T03 allocated population
sum(block cohort)     = sum(zone cohort)     = T04 cohort total
sum(block jobs)       = sum(zone jobs)       = T05 total jobs
```

Building counts are preserved as well. Numeric job totals use finite non-negative values
and tolerance only for floating-point summation.

## Scope boundary

T06 performs no raster calibration, spatial redistribution, infrastructure-demand mapping,
persistence, API, or UI. Population raster sampling starts at S09-T07.
