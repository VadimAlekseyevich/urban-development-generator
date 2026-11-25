# S07-T03 — Block metrics

`core.urban_generator.blocks.BlockMetricsCalculator` calculates deterministic raw geometry metrics for the developable block candidates produced by S07-T02.

## Input and CRS

The calculator accepts an immutable `BlockDevelopableClippingResult` and requires the same explicit projected working CRS. All distance and area values therefore use project metric units; EPSG:4326 is rejected by the shared CRS guard.

The input block count is bounded by `DEFAULT_MAX_METRIC_BLOCKS` (configurable per calculator instance).

## Raw metrics

For every `DevelopableBlockCandidate` the calculator returns the original immutable candidate plus:

- `area_m2` — polygon area;
- `perimeter_m` — total polygon boundary length, including interior rings;
- `compactness` — Polsby-Popper `4πA / P²`, clamped only for floating-point overflow above the theoretical upper bound `1`;
- `aspect_ratio` — long side divided by short side of the minimum rotated rectangle, always `>= 1`;
- `hole_count` — number of polygon interior rings.

The result also contains aggregate diagnostics: block count, total area, total perimeter, number of blocks with holes, and total hole count. `math.fsum` is used for stable aggregate floating-point sums.

These are raw measurements. S07-T03 does not classify blocks as good/bad, oversized, accessible, or slivers and does not create a composite score.

## Determinism and provenance

Input rows are ordered by stable `block_id`; duplicate IDs are rejected. Each `MeasuredBlock` retains the exact S07-T02 candidate, including `source_block_id`, fragment index and geometry.

## Explicit work-item boundary

S07-T03 does **not** implement:

- road frontage or access validation (`S07-T04`);
- oversized-block thresholds or splitting (`S07-T05`);
- sliver merge/drop policy (`S07-T06`);
- zone association, parcel subdivision, persistence, API or UI.
