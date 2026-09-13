# Suitability configuration and factor contract (S04-T05)

S04-T05 establishes the configuration and factor boundary used by the suitability pipeline before
implementing the concrete slope, road-proximity, and land-use factors.

## Versioned configuration

`SuitabilityConfig` is immutable and carries an explicit `version`. It contains:

- an ordered tuple of factor configurations;
- one non-negative weight per factor, with at least one positive weight;
- an explicit normalization policy per factor;
- final normalized-score thresholds;
- a deterministic SHA-256 content fingerprint for provenance/cache keys.

Factor codes are unique stable identifiers. The configured order is preserved for diagnostics, while
`normalized_weights` always sum to `1.0`; callers do not need to hand-normalize human-friendly weights.

## Normalization

Raw factor values are not assumed to share units. Each `SuitabilityFactorConfig` declares one policy:

- `IDENTITY`: the factor already emits values in `0..1`;
- `MIN_MAX`: map `raw_min..raw_max` linearly to `0..1`, clipping values outside the range;
- `INVERTED_MIN_MAX`: the same mapping with the direction reversed, useful when lower raw values are
  better (for example slope).

The weighted aggregator planned in S04-T10 therefore receives an explicit normalization contract
instead of embedding factor-specific magic constants.

## Thresholds

`SuitabilityThresholds.minimum_score` is the minimum final normalized score considered acceptable.
`preferred_score` is optional and, when set, must be at least the minimum score. Both are bounded to
`0..1`.

Hard exclusions remain separate. They are introduced by S04-T06 and take precedence over weighted
scores; score thresholds must not be used as a substitute for hard constraints.

## Factor protocol

Every factor exposes stable `code` and `version` fields and implements:

```text
evaluate(grid, snapshot, context) -> SuitabilityFactorResult
```

All factors receive the same `SuitabilityGridSpec`, which has:

- an explicit projected/metre `working_srid`;
- metric bounds;
- positive raster width and height;
- derived cell width/height and cell count.

`SuitabilityFactorResult` contains a 2D raw-value raster and a separate validity mask. Valid cells
must be finite. Arrays are copied and made read-only when the result is created, preventing accidental
mutation after a factor returns. Nodata/invalid cells are represented through the validity mask rather
than by guessing sentinel values.

`validate_factor_result()` is the orchestration guard: result code/version/grid must match the factor
and requested grid exactly.

## Scope boundary

This task does not implement:

- hard exclusion rasterization (S04-T06);
- DEM slope calculation (S04-T07);
- road proximity calculation (S04-T08);
- land-use scoring (S04-T09);
- weighted aggregation (S04-T10);
- raster artifact/API/UI (S04-T11..T12).

Those tasks build on this contract without changing factor identity, normalization, or grid semantics.
