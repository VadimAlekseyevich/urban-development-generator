# Residential capacity per building

S09-T02 derives deterministic residential capacity from authoritative S08 building
attributes and GFA. It does not allocate integer residents or reconcile a project-wide
population target.

## Input

Each `ResidentialCapacitySubject` joins:

- S08-T11 `BuildingAreaMetrics` for authoritative `gfa_m2` and floors;
- S08-T10 `AssignedBuildingAttributes` for canonical building use.

Building ids and floor counts must match exactly across both inputs.

## Calculation

For each building:

- `residential` use treats 100% of GFA as residential;
- `mixed` use applies `DemographicScenario.residential_gfa_share`;
- `public` and `commercial` uses have zero residential GFA.

Then:

```text
residential_gfa
× occupancy_ratio
= occupied_residential_area

occupied_residential_area
÷ residential_area_per_person_m2
= resident_capacity

resident_capacity
÷ average_household_size
= household_capacity
```

Capacities remain fractional. T02 deliberately performs no integer rounding so T03 can
allocate a scenario target while preserving totals and capacity constraints.

## Provenance and determinism

Subjects are canonicalized by `building_id`. The result records demographic scenario
version and fingerprint. Population target, age-group shares and working ratio do not
change physical building capacity.

## Scope boundary

S09-T02 contains no population target reconciliation, resident assignment, age-group
allocation, jobs estimate, aggregation, raster calibration, persistence, API or UI.
