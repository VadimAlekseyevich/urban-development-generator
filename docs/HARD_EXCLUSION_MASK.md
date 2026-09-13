# Hard exclusion mask (S04-T06)

S04-T06 converts relevant hard spatial constraints into one deterministic boolean raster aligned to the shared suitability grid.

## Contract

`HardExclusionMask.excluded` is a 2D immutable boolean array with the same shape as `SuitabilityGridSpec`:

- `True` means the cell is forbidden and must not receive a usable suitability score;
- `False` means the cell remains eligible for later soft-factor scoring;
- the hard mask stays separate from soft scores so hard constraints always take precedence.

The mask combines three source types:

1. project boundary — cells whose centers are outside the boundary are excluded;
2. named vector hard-exclusion layers — water, protected areas, constraint geometry, or future equivalent sources;
3. named precomputed raster hard masks — already aligned boolean masks, for example a future raster threshold result.

All sources must use the exact project metric CRS/grid contract. The builder never guesses or reprojects CRS.

## Rasterization policy

Boundary membership always uses cell-center semantics. This avoids treating a cell as inside the project merely because a small corner touches the boundary.

Vector exclusion layers choose one explicit policy:

- `CELL_CENTER` — a cell is excluded when its center is covered by an exclusion geometry;
- `ANY_TOUCH` — conservative default; a cell is excluded when any part of it is touched by an exclusion geometry.

The policy is explicit so later suitability runs can reproduce the same mask exactly.

## Resource bounds

The builder rejects work before rasterization when:

- `grid.cell_count > max_cells` (default `25_000_000`);
- total vector exclusion geometry count exceeds `max_shapes` (default `100_000`).

Raster layers must already match the target grid exactly. They are combined with boolean OR and are defensively copied into immutable core values.

## Determinism and provenance

Source order is retained in `source_codes`, beginning with `boundary`. Duplicate codes are rejected so diagnostics/provenance cannot become ambiguous.

Given the same grid, geometries, raster layers, and rasterization policy, the resulting boolean mask is deterministic.

## Out of scope

S04-T06 does not calculate slope, road proximity, landuse weights, weighted suitability scores, persist raster artifacts, or expose a UI layer. Those remain S04-T07 through S04-T12.
