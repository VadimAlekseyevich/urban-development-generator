# Spatial generation composition (#202)

The spatial registry binds the first eight canonical stages, from prepare_snapshot
to demography. Source reads are run-linked PostGIS and normalized DEM. Suitability
uses configured slope, road proximity and categorical landuse factors. The v2
suitability config requires explicit class weights when landuse is enabled.

Zoning uses a polygonized mask of valid cells meeting the configured minimum
score, clipped to the actual boundary and exact polygonal hard exclusions.
The derived zoning validity mask prevents selecting below-threshold cells;
original suitability remains available for reporting and road cost calculations.
The polygon count and coordinate count are bounded. Road validation receives
hard geometries and fixed building footprints; this detects crossing rather
than claiming every route is automatically repaired. Blocks use the zoning
developable geometry; building spacing uses actual fixed footprints.

SpatialOutputPublisher delegates zones, roads, blocks/parcels, buildings and
demography to the existing writers in dependency order. Each writer owns a
transaction. This is not yet the deployed lifecycle/publication boundary.
The test executes real PostGIS-selected roads and landuse through all eight
stages, persists output and verifies run-scoped rows, without falsely marking
the incomplete run succeeded. The prior DEM integration test covers GeoTIFF.

Not yet supported in this composition: fixed zones, actual demographic
baselines, existing building GFA baseline allocation, suitability artifact
publication, infrastructure, final validation, metrics, manifest, full worker
startup and cancel/retry-safe output publication. Do not register the partial
registry as a production worker or declare release acceptance.
