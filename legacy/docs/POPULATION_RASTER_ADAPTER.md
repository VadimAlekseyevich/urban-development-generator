# Population raster calibration adapter

S09-T07 adds an **optional** windowed population-raster port. The core demographic model
from S09-T01 through S09-T06 remains fully usable without a raster source.

## Source contract

A `PopulationRasterSource` exposes only:

- working CRS;
- raster width/height;
- metric cell area;
- value semantics;
- bounded `read_window`.

The core sampler does not import `rasterio`, open files, or know GeoTIFF paths. A storage
or rasterio adapter may implement the protocol outside the base demographic model.

## Value semantics

Two explicit source types are supported:

- `population_per_cell` — the value is already people represented by one cell;
- `density_per_km2` — the value is people/km² and is converted through
  `cell_area_m2`.

Both are normalized to a per-subject `sampled_population` and
`mean_density_per_km2`.

## Bounded sampling and nodata

Sampling is requested through immutable pixel windows. Before the first source read the
sampler validates:

- subject/window count limits;
- total requested cell limit;
- source bounds;
- working CRS;
- duplicate subject IDs.

Windows inside one subject may not overlap, preventing accidental double counting.

`nodata` is either ignored or rejected by explicit policy. An all-nodata subject is not
turned into NaN/Inf: it returns zero valid area/population/density with
`has_valid_data == False`.

## Scope boundary

T07 only supplies normalized external evidence. It does not modify S09-T06 population,
cohorts or jobs. Spatial redistribution while preserving demographic totals belongs to
S09-T08.
