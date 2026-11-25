# Zoning region growth/refinement

S05-T06 adds a deterministic bounded refinement stage after base partitioning and initial zone assignment.

## Contract

`BoundedRegionRefiner` receives:

- a `ZoningPartitionResult` with immutable polygon geometry;
- a `ZoneAssignmentResult` with one functional class per partition cell;
- the same versioned `ZoningConfig` used for assignment;
- a positive `max_iterations` bound.

The refiner changes labels only. Partition geometry is never mutated, merged, split, or regenerated.

Cells are adjacent only when their polygon boundaries share positive metric length. Point-only contact is not treated as adjacency.

## Refinement objective

Each iteration considers moving one cell to a class already present on one of its adjacent cells. A move is accepted only when it strictly improves this deterministic lexicographic objective:

1. number of `FORBIDDEN` cross-class adjacency edges;
2. number of connected same-class regions below that class' `minimum_area_m2`;
3. total missing area of those undersized regions;
4. number of `DISCOURAGED` cross-class adjacency edges;
5. negative count of `PREFERRED` cross-class adjacency edges, so more preferred edges are better;
6. total absolute target-area error against configured `target_share` values.

Equal candidate moves are resolved by partition cell index and canonical `ZoneClass` order.

Because every accepted move strictly improves a finite labeling objective, the algorithm cannot cycle. Work is additionally bounded by `max_iterations`.

## Convergence diagnostics

`ZoneRefinementResult` records:

- initial and final objective values;
- applied iteration count and configured bound;
- `converged` plus `stop_reason` (`stable` or `max_iterations`);
- number of cells whose final class differs from the initial assignment;
- adjacency edge count;
- refinement strategy version.

`converged=true` means there is no single adjacent-class cell move that improves the refinement objective. It does not claim global optimality.

## Boundary with S05-T07

This stage uses zoning config fields as refinement heuristics. It is **not** a second constraint engine and does not produce `ConstraintResult` values.

S05-T07 applies zoning rules through the existing common `ConstraintEngine` contract and is the authoritative rule-evaluation layer. Keeping refinement and validation separate avoids duplicate local constraint checks.

## Scope exclusions

S05-T06 does not add persistence, API endpoints, generated-zone storage, run selection, map visualization, or city-specific zoning policy. Minimum areas are metric values and therefore rely on partition geometry already being in the project working metric CRS.
