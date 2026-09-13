# S04-T09 — Landuse factor

`LanduseFactor` converts an already aligned categorical land-use grid into normalized suitability scores using an explicit, versioned class mapping.

## Contract

- The source exposes the exact `SuitabilityGridSpec` used by the factor and supports bounded window reads.
- Source cells contain canonical class names (`str`) or `None` for nodata.
- `LanduseClassWeights` maps exact class names to scores in `0..1` and carries its own version plus stable SHA-256 fingerprint.
- Factor output is already normalized to `0..1`, so the matching `SuitabilityFactorConfig` should normally use `IDENTITY` normalization.
- The source grid, snapshot working CRS, and run-context working CRS must agree.
- The factor is independent of OSM, PostGIS, FastAPI, and any city-specific taxonomy. Adapters are responsible for converting source data into canonical class names.

## Unknown classes and nodata

Unknown source classes are never handled implicitly:

- `INVALIDATE` marks an unmapped class invalid in `valid_mask`.
- `USE_DEFAULT` assigns the configured `default_score` and keeps the cell valid.

`None` always means nodata and remains invalid, even with `USE_DEFAULT`. This keeps “unknown category” distinct from “no source observation”.

## Bounded execution

The factor checks `max_cells` before allocating output arrays or reading the source. It then traverses the grid in deterministic row-major windows of at most `tile_size × tile_size` cells and writes each mapped result directly into the corresponding output slice.

Default guards:

- `tile_size = 256`
- `max_cells = 25_000_000`
- maximum `tile_size = 4096`

## Example mapping

A demo/reference mapping may look like:

```python
LanduseClassWeights(
    version="demo-v1",
    classes=(
        LanduseClassWeight("residential", 0.90),
        LanduseClassWeight("commercial", 0.80),
        LanduseClassWeight("industrial", 0.30),
        LanduseClassWeight("forest", 0.10),
    ),
    unknown_policy=LanduseUnknownClassPolicy.USE_DEFAULT,
    default_score=0.40,
)
```

For manual demo work, Ryazan remains the project reference city documented in `docs/DEMO_REFERENCE.md`; the core mapping above deliberately contains no Ryazan-specific rules.

## Scope boundary

S04-T09 does not decide how raw OSM/municipal tags are classified into canonical names and does not combine this factor with slope, road proximity, or hard exclusions. Weighted aggregation is S04-T10.
