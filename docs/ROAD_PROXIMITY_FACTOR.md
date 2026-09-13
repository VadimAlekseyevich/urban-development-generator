# S04-T08 — Road proximity factor

`RoadProximityFactor` is the soft suitability factor that measures the exact planar distance from each suitability-grid cell center to the nearest source road.

## Contract

- Input roads are immutable Shapely `LineString`/`MultiLineString` geometries.
- `RoadProximityIndex.working_srid`, `SuitabilityGridSpec.working_srid`, the snapshot CRS, and the run-context CRS must match.
- The working CRS must be projected and metre-based through the shared `require_working_crs` guard.
- Factor output values are raw distances in metres. Normalization to `0..1` remains the responsibility of `SuitabilityFactorConfig` and the later weighted aggregator.
- An empty road index produces an explicitly invalid factor (`valid_mask=False` for every cell) instead of silently treating missing roads as zero distance or infinite suitability.

## Distance semantics

Distances are measured from raster cell centers to the nearest road geometry using Shapely `STRtree.query_nearest`. The result is an exact Cartesian geometry distance in the project working CRS; roads are not first rasterized, so there is no cell-size approximation in the distance itself.

Grid rows follow raster order from north/top to south/bottom. For bounds `(min_x, min_y, max_x, max_y)`, cell centers are:

- `x = min_x + (col + 0.5) * cell_width_m`
- `y = max_y - (row + 0.5) * cell_height_m`

## Bounded execution

The factor does not build a `grid cells × road features` distance matrix.

1. Roads are indexed once in an `STRtree`.
2. The target grid is traversed lazily in deterministic row-major tiles.
3. Each tile creates at most `tile_size²` point geometries and runs one vectorized nearest-neighbour query.
4. The returned distances are written directly into the corresponding output slice.

Default guards:

- `max_roads = 500_000` on the reusable road index;
- `tile_size = 256`, with a hard maximum of `512`;
- `max_cells = 25_000_000`, checked before output-array allocation or nearest queries.

These guards are operational safety limits, not modelling assumptions.

## Determinism

Road input order does not affect the distance value. When multiple road geometries are exactly equidistant, the index may select any one of them, but the reported nearest distance is identical. Tile traversal and output placement are deterministic.

## Suitability configuration

A typical road-proximity configuration uses `MIN_MAX` normalization so larger raw distances map to larger normalized values only when that is the intended modelling meaning. If closeness to roads should be preferred, use `INVERTED_MIN_MAX` instead. The factor itself deliberately does not encode that policy.

For example, a project can interpret `0..1000 m` with `INVERTED_MIN_MAX`, clipping distances beyond `1000 m` during suitability normalization. The range is project configuration, not a hard-coded Ryazan or global default.

## Scope boundary

S04-T08 only provides the reusable core factor and index. It does not add the weighted suitability aggregator, persistence artifact, API endpoint, or map rendering. Those remain S04-T10 through S04-T12.
