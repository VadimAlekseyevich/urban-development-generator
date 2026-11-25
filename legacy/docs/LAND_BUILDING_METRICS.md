# Land/building raw metric adapter

> **Status: Implemented through UG-AI-051 / S11-T05**

S11-T05 projects existing authoritative suitability, block and building outputs into the
canonical `MetricSource.LAND_BUILDING` raw metrics. It does not rerun placement, suitability,
block generation or building area calculation and it does not introduce metric IDs outside
`RawMetricId`.

## Inputs

`LandBuildingMetricAdapter` consumes:

- `WeightedSuitabilityResult`;
- `BlocksAndParcelsStageOutput`;
- `BuildingStageOutput`.

The three inputs must use the same metric working SRID. Generated building IDs from authoritative
attribute and area results must match exactly.

## Metric sources

The adapter emits every `MetricSource.LAND_BUILDING` definition in canonical registry order:

- `land.developable_area_m2` — `valid_count × grid cell width × grid cell height` from the final
  suitability validity mask. Hard-excluded and invalid-data cells are not counted as developable.
- `land.developed_area_m2` — exact post-cleanup block area from
  `SliverCleanupDiagnostics.output_area_m2`.
- `land.green_recreation_share` — total stored `BuildingStageBlockRef.area_m2` for
  `ZoneClass.RECREATION` divided by developed block area. Zero developed area yields zero share.
- `buildings.coverage_ratio` — `BuildingAreaSummary.coverage_ratio`.
- `buildings.far` — `BuildingAreaSummary.far`.
- `buildings.gfa_m2` — `BuildingAreaSummary.total_gfa_m2`, including the existing-building
  baseline already incorporated by S08.
- `buildings.archetype_distribution` — deterministic shares over every canonical
  `BuildingArchetype` from `BuildingAttributeAssignmentResult`.

The adapter performs only bounded arithmetic/count aggregation over existing typed values.
It does not call Shapely geometry operations, rebuild rasters or rerun building metrics.

## Archetype distribution boundary

S08 has typed archetypes only for generated buildings. Fixed snapshot building refs do not
currently expose canonical archetype metadata. Therefore the S11-T05 archetype distribution is
explicitly generated-only; diagnostics record both generated count and fixed-ref count and set
`archetype_distribution_includes_fixed=False`.

All canonical archetype categories are emitted in enum declaration order. For an empty generated
set, each category has count/share zero. Otherwise shares sum to one.

## Output contract

`LandBuildingRawMetricsResult` contains:

- exactly the seven registry-owned land/building IDs in registry order;
- scalar values for the six scalar metrics;
- one typed `BuildingArchetypeShare` tuple for the distribution metric;
- audit diagnostics for developable cells, developed/recreation area, block count and
  generated/fixed building counts.

Normalization, composite scoring, persistence/API schemas and treatment of missing fixed
archetypes remain outside S11-T05.
