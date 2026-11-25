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
demography to the existing writers. TransactionalSpatialMaterializer uses the
existing SQL state store completion transaction and nested writer savepoints
on the same connection, so stage success and output rows commit together.
Cancellation is checked under the existing run/job locks before writing.
Suitability is serialized by the existing two-band GeoTIFF writer, then
published through the canonical artifact publisher and linked to the stage.
Blob promotion remains external to SQL; orphan GC reclaims unreferenced
bytes after rollback. The test executes real PostGIS-selected roads and
landuse through eight stages and verifies rows, stage states and raster
ownership without marking the incomplete run succeeded. The prior DEM test
covers GeoTIFF inputs. Production worker startup is not yet installed.

Not yet supported in this composition: fixed zones, actual demographic
baselines, existing building GFA baseline allocation, infrastructure,
final validation, metrics, manifest, full worker startup and full crash/retry
acceptance. Do not register the partial
registry as a production worker or declare release acceptance.
