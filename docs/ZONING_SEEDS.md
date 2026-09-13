# Deterministic zoning seeds

S05-T03 adds the first generated-zoning primitive: deterministic, suitability-aware seed points for later partition geometry.

## Contract

`DeterministicZoningSeedGenerator` consumes:

- a canonical `WeightedSuitabilityResult`;
- the current `RunContext`;
- an explicit positive seed `count`.

It returns a `ZoningSeedSet` containing unique raster-cell seeds, RNG provenance, and counts describing weighted versus zero-score fallback selection.

The generator is infrastructure-independent and does not read files, query the database, or depend on web/API state.

## Determinism

The RNG stream is created only through `RunContext.rng("zoning.seeds.v1")`.

Consequences:

- same run seed + same suitability raster + same count => same selected seed cells;
- unrelated RNG namespaces do not perturb zoning seed selection;
- changing the run seed can change the selected cells;
- selected seeds are returned in canonical row-major order so downstream object identity does not depend on RNG draw order.

The derived RNG seed is returned in `ZoningSeedSet.rng_seed` for provenance/debugging.

## Suitability-aware selection

Only cells with `valid_mask=true` are eligible. Hard-excluded and invalid-data cells therefore cannot become zoning seeds.

Selection is without replacement:

1. valid cells with `score > 0` are sampled with probability proportional to their suitability score;
2. if the requested count exceeds the number of positive-score valid cells, every positive-score cell is retained and the remainder is sampled uniformly from valid zero-score cells;
3. requesting more seeds than valid cells is an explicit error.

This keeps suitability influential while still allowing a caller to request complete coverage of an otherwise valid zero-score region when necessary.

## Coordinates and CRS

Each seed is located at the center of its selected raster cell. Raster row `0` follows the same north-up convention as the suitability GeoTIFF (`rasterio.transform.from_bounds`):

- `x = min_x + (col + 0.5) * cell_width`;
- `y = max_y - (row + 0.5) * cell_height`.

`RunContext.working_srid` must exactly match the suitability grid SRID. The coordinates are therefore metric project coordinates, not longitude/latitude.

## Scope boundary

S05-T03 does **not** create Voronoi polygons, assign zone classes, enforce target shares, refine regions, persist generated zones, or expose UI. Those are later S05 work items.

The Ryazan reference city may be used later as a real-data manual check, but this core algorithm contains no Ryazan-specific CRS, geometry, thresholds, or assumptions.
