# DEM slope factor

`core.urban_generator.suitability.dem_slope` implements roadmap task **S04-T07**.
It calculates a raw suitability factor from an elevation raster without coupling core logic to
FastAPI, SQLAlchemy, Redis, filesystem paths, or a concrete Rasterio dataset handle.

## Contract

`DEMSlopeSource` exposes an already aligned `SuitabilityGridSpec` and a bounded
`read_window()` operation. Infrastructure code is responsible for resolving the snapshot DEM
artifact and exposing it through this port. The source must map raster nodata to `None`.

`DEMSlopeFactor` returns `SuitabilityFactorResult` with:

- factor code `slope` by default;
- raw values in **degrees**, not a 0..1 suitability score;
- an explicit `valid_mask`;
- deterministic diagnostics describing reads, validity, nodata policy, and vertical scale.

Normalization is intentionally left to `SuitabilityFactorConfig` / the future weighted
aggregator. A common configuration is an inverted min/max normalization because flatter terrain
is usually more suitable, but S04-T07 does not hard-code that planning decision.

## Windowing

The factor evaluates row-major output tiles. Each requested DEM window adds a one-cell halo when
one exists. That halo lets central finite differences cross tile boundaries without ever loading
the complete DEM only for local slope calculation.

`tile_size` is bounded to 4096 and `max_cells` is checked before allocating output arrays or
reading the source. This keeps the iteration and memory contract explicit.

## Slope calculation

Horizontal distances come from the metric `SuitabilityGridSpec` cell width/height. Elevations are
multiplied by `vertical_scale_to_m` before differentiation, so a source stored in centimetres can
use `0.01`, for example.

For valid derivatives:

```text
gradient = hypot(dz/dx, dz/dy)
slope_degrees = degrees(atan(gradient))
```

Interior cells use central differences when both neighbours are available. Physical raster edges
use the available one-sided derivative so the outer row/column is not discarded solely because
there is no cell outside the raster.

## Nodata policy

`DEMSlopeNoDataPolicy.INVALIDATE` is conservative: an interior derivative becomes invalid when a
required neighbour is nodata. `ONE_SIDED` may use the remaining valid neighbour for that axis.
The centre elevation itself must always be valid, and each axis must still have enough data to
form a derivative.

Invalid output cells remain separate from hard exclusions. They are represented by
`valid_mask=False`; later suitability aggregation decides how missing factor values are handled.

## Out of scope

This task does not add DEM artifact resolution, the road-proximity factor, landuse weighting,
weighted aggregation, suitability artifact persistence, API endpoints, or map visualization.
Those remain subsequent S04 tasks.
