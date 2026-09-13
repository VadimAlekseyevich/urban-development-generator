# S04-T10 — Weighted suitability aggregator

`aggregate_weighted_suitability` combines already-evaluated factor rasters into the canonical soft suitability score for one `SuitabilityGridSpec`.

## Score contract

- Output scores are always finite and inside `0..1`.
- Factor normalization is explicit and comes only from `SuitabilityFactorConfig`:
  - `IDENTITY` requires every valid raw value to already be inside `0..1`;
  - `MIN_MAX` maps `raw_min..raw_max` to `0..1` and clips outside values;
  - `INVERTED_MIN_MAX` applies the same clipping and then inverts the normalized value.
- Positive factor weights are divided by `SuitabilityConfig.total_weight`; therefore the final score is a convex weighted sum.
- Factors configured with weight `0` do not affect the score and do not have to be supplied to the aggregator.
- Results for factor codes that are not present in the config are rejected.

The aggregator does not infer normalization from observed raster minima/maxima. This keeps the same inputs/config reproducible and avoids dataset-dependent score drift.

## Missing-data policy

A developable cell is valid only if every positive-weight factor is valid for that cell.

Missing factor data never causes local renormalization of the remaining weights. If any required factor is invalid, the aggregate cell is marked invalid and its stored score is exactly `0`.

This makes scores comparable across the raster: a score of `0.7` always uses the same configured factor weights rather than a cell-specific subset.

## Hard-mask precedence

`HardExclusionMask` is applied independently of the soft weighted score and always wins:

- hard-excluded cell => `valid_mask=False`;
- hard-excluded cell => stored `score=0`;
- soft factors cannot make an excluded cell developable.

The result keeps a separate immutable `hard_excluded_mask`, so downstream code can distinguish hard exclusions from cells invalidated by missing factor data.

## Result and provenance

`WeightedSuitabilityResult` stores:

- target grid;
- immutable score raster;
- immutable aggregate validity mask;
- immutable hard-exclusion mask;
- suitability config version and SHA-256 fingerprint;
- positive-weight factor `(code, version)` pairs in configured order;
- compact diagnostics with valid, hard-excluded, and invalid-data counts.

The canonical persisted raster artifact, statistics, and broader provenance record belong to S04-T11.

## Bounded execution

The aggregator validates `max_cells` before allocating its output arrays. It processes one factor raster at a time and never constructs a `cells × factors` matrix. Default limit: `25_000_000` cells.

## Scope boundary

This task does not persist a raster, publish an API endpoint, render a heatmap, or calculate artifact statistics. Those are S04-T11 and S04-T12.
