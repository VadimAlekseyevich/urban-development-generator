# Zoning region refinement (S05-T06)

`DeterministicZoneRegionRefiner` refines the functional-zone labels produced by S05-T05 while keeping the S05-T04 partition geometry immutable.

## Scope

The refiner operates on:

- a `ZoningPartitionResult`;
- a matching `ZoneAssignmentResult`;
- the same versioned `ZoningConfig` used by assignment;
- an explicit bounded `max_iterations` value (default `256`, hard upper bound `10_000`).

It does **not** create, split, merge, buffer, or otherwise mutate partition polygons. A refinement move only changes the `ZoneClass` attached to an existing partition cell.

## Region topology

Cell adjacency is built with `STRtree`, so the stage does not perform a full spatial N×M comparison. Two cells are adjacent only when they share positive boundary length; point-only contact does not count.

A region is a connected component of adjacent cells carrying the same `ZoneClass`. `minimum_area_m2` is evaluated on the total area of that connected region in the project working metric CRS.

## Refinement objective

Each iteration considers deterministic boundary relabels around current problems. Candidate labels are restricted to classes already present on adjacent cells, so refinement grows/merges neighboring regions instead of inventing disconnected labels.

The lexicographic objective reduces, in order:

1. forbidden adjacency edges;
2. number of regions below configured minimum area;
3. total minimum-area deficit;
4. absolute target-share area error;
5. discouraged adjacency edges;
6. and, only as a final tie-break, prefers more configured `PREFERRED` adjacencies.

For equivalent objective values, lower-suitability cells are changed before higher-suitability cells, then the candidate with more shared boundary is preferred, followed by stable cell/class order. This preserves the priority established by S05-T05 without assigning any city-specific meaning to a particular functional class.

Only a strict objective improvement is accepted, so the loop cannot oscillate. The explicit iteration bound additionally guarantees termination.

## Convergence diagnostics

`ZoneRefinementDiagnostics` records:

- configured maximum iterations;
- applied move count and exact moves;
- initial/final forbidden, discouraged and preferred adjacency counts;
- initial/final under-minimum region counts and area deficits;
- initial/final target-share absolute area error;
- termination reason: `CONVERGED`, `STALLED`, or `MAX_ITERATIONS`.

`converged=True` means there are no forbidden adjacency edges and no connected regions below their configured minimum area. Soft `DISCOURAGED`/`PREFERRED` policies are still reported but do not make a result non-converged.

## S05-T07 boundary

This task intentionally does **not** invoke `ConstraintEngine`. T06 owns bounded region-relabel mechanics and the zoning semantics already present in `ZoningConfig`. S05-T07 is responsible for applying registered zone constraints through the shared `Constraint`/`ConstraintEngine` contract, without adding one-off engine checks inside the refiner.

## City neutrality

The algorithm contains no Ryazan-specific class preference, thresholds, CRS, target shares, or adjacency assumptions. Ryazan may be used later as a reference dataset for manual validation, but all configuration remains project/data driven.
