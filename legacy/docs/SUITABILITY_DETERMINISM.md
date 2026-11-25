# Suitability determinism and property invariants

S04-T13 closes the suitability sprint by testing determinism as a public behavioral property rather
than as one hand-picked fixture.

## Determinism contract

For the same target grid, suitability configuration, hard exclusions, factor values and validity
masks, suitability evaluation must produce the same semantic result:

- identical final score cells;
- identical validity and hard-exclusion masks;
- identical factor version ordering and diagnostics;
- identical canonical artifact statistics and provenance;
- identical score/status pixels and GeoTIFF metadata after serialization.

The input order of `SuitabilityFactorResult` values is intentionally not significant. Aggregation
uses the versioned factor order from `SuitabilityConfig`, so permuting the supplied result tuple does
not change the output raster.

Factor implementations that process bounded windows or indexed batches must also be independent of
that partitioning. The tests therefore evaluate DEM slope and landuse with different tile sizes, and
road proximity with different tile sizes plus reversed road feature order.

## Hard-mask invariants

Hard exclusions are not another weighted factor. The following invariants are tested across multiple
fixed randomized cases:

1. adding another hard exclusion can only preserve or increase the excluded set;
2. OR-ing the same exclusion twice is idempotent;
3. reordering raster exclusion sources does not change excluded cells;
4. cells outside the project boundary are excluded according to cell-center semantics;
5. a hard-excluded cell can never be valid in the final suitability result;
6. every hard-excluded or otherwise invalid cell has final score `0`;
7. valid, hard-excluded and invalid-data counts partition the complete grid.

## Property-style test generation

`tests/unit/test_suitability_determinism_properties.py` uses local
`numpy.random.default_rng(seed)` instances with checked-in fixed seeds. This provides varied grids,
values, nodata patterns and masks while keeping CI runs exactly reproducible and avoiding global RNG
state.

No additional property-testing dependency is required for this work item, so `uv.lock` and the
runtime dependency graph remain unchanged.

## Artifact reproducibility boundary

Canonical artifacts are compared semantically. Two writers using different valid internal tile sizes
must produce the same score raster, status raster, CRS/transform, tags, statistics and provenance.

The raw GeoTIFF byte stream and checksum are not required to match when internal tiling differs,
because container layout and compressed block boundaries are encoding details rather than suitability
semantics. With an identical writer configuration the normal artifact store checksum still provides
content integrity.

## Scope

This work item adds tests and documents existing behavior. It does not introduce a new factor,
change scoring policy, add API behavior, or start S05 functional zoning.
