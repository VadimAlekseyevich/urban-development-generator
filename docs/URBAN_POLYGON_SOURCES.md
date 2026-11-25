# P0 #202 — landuse and fixed building input

SqlAlchemyUrbanPolygonSourceReader resolves exact run-linked ready landuse
and building polygons from PostGIS, including OSM versions with source rows.
It verifies project, CRS, boundary digest, and complete selected version set.
Reads are spatially filtered by project boundary with strict feature and
coordinate budgets; geometries are preserved, not clipped or repaired.

VectorLanduseSource implements the existing LanduseSource window protocol.
Cell centres are rasterized on the supplied metric grid. Uncovered cells and
overlaps between distinct classes are NoData; same-class overlaps remain
valid. Class scores are supplied separately by the existing LanduseFactor
configuration, not inferred. This slice does not support categorical GeoTIFF.

Building polygons become PlacedBuildingFootprint values for the canonical
spacing index. FROM_SCRATCH rejects fixed buildings. Existing buildings
are not treated as generated output; source-specific coverage/GFA and
population baselines, road/building interactions and worker orchestration
remain separate integration tasks.

Tests exercise actual persisted PostGIS polygons, overlap and tiling, the
canonical landuse factor and spacing index, invalid version and budget
rejections. This is not full production generation acceptance.
