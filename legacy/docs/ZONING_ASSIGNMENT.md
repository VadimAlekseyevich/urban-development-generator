# Functional zoning assignment

S05-T05 assigns functional zone classes to the immutable partition cells produced by S05-T04. Assignment is deliberately separate from partition geometry: this stage returns cell references and labels, never replacement polygons.

## Inputs

`SuitabilityTargetShareAssigner.assign(...)` requires:

- a `ZoningPartitionResult` in the project working metric CRS;
- a versioned `ZoningConfig` with canonical target shares.

Each partition cell already carries the suitability score of the deterministic seed that generated it. T05 uses that score as the v1 suitability priority for the cell.

## Deterministic strategy

1. Partition cells are considered in descending seed suitability score.
2. Equal suitability scores are ordered by `seed_index`.
3. For each cell, the zone class with the largest remaining target-area deficit is selected.
4. Equal deficits prefer the larger configured target share, then canonical `ZoneClass` order.
5. Results are returned in partition `cell_index` order so downstream stages can join assignment back to geometry without depending on processing order.

Target area is `partition.developable_area_m2 * target_share`. Because partition cells are indivisible in T05, exact target shares are not always achievable. `ZoneShareDiagnostic` records target area, achieved area/share, cell count, and absolute area error for every class.

## Suitability semantics

The current suitability surface is a general development-suitability score, not a class-specific score. T05 therefore does **not** hard-code assumptions such as "high suitability means residential" or city-specific land-use policy. Suitability controls which cells are considered first; configured target-area deficits control which class they receive.

If future configurations introduce class-specific suitability models, they can replace the prioritization policy without changing partition geometry.

## Geometry separation

`ZoneAssignment` contains:

- `cell_index`;
- `seed_index`;
- `zone_class`;
- `area_m2`;
- `suitability_score`.

It intentionally contains no geometry. The authoritative polygon remains in `ZoningPartitionResult.cells[cell_index]`.

## Scope boundaries

T05 does not enforce `minimum_area_m2` or adjacency rules. It does not grow/merge regions, invoke the common constraint engine, persist `GeneratedZone`, or expose an API/UI. Those belong to S05-T06 through S05-T09.

The assignment output is a deterministic base labeling that later refinement stages may improve while preserving the fixed source-state semantics established earlier in the sprint.
