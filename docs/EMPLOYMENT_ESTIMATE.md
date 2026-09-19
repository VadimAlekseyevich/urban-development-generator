# Jobs and workforce estimate

S09-T05 estimates two deliberately separate quantities:

- job capacity supported by generated non-residential floor area;
- workforce associated with the residents actually allocated by S09-T03.

Neither value is treated as an observed census count.

## Explicit job-density assumptions

`EmploymentConfig` is versioned and requires one `JobDensityRule` for each job-supporting
building use:

- `mixed`;
- `public`;
- `commercial`.

Every rule supplies positive `area_per_job_m2`. Residential buildings intentionally have
no job-density rule and produce zero job estimate.

For mixed buildings, only the non-residential share of GFA supports jobs:

```text
job_supporting_gfa = total_gfa × (1 - residential_gfa_share)
```

Public and commercial buildings use their full GFA. Approximate jobs are then
`job_supporting_gfa / area_per_job_m2`.

## Workforce

Generated workforce is based on residents that were actually allocated, not the requested
scenario target:

```text
generated_workforce_estimate
= allocated_generated_population × working_population_ratio
```

This keeps capacity shortfalls visible instead of manufacturing workers for residents who
could not be placed.

## Determinism and provenance

Subjects are sorted by stable `building_id`. `EmploymentConfig` canonicalizes rule order
and exposes a SHA-256 fingerprint. The result records both employment-config and
demographic-scenario provenance.

## Scope boundary

T05 does not force jobs and workforce to balance and does not assign workers to workplaces.
It does not aggregate by block/zone, calibrate from rasters, persist results, or expose
API/UI. Aggregation remains S09-T06.
