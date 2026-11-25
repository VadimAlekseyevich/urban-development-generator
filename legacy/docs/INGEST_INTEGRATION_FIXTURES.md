# Ingest integration fixtures

S03-T15 defines a deterministic fixture matrix for every ingest format introduced in Sprint S03.
The suite is intentionally small enough to run in ordinary CI without network access.

`tests/fixtures/ingest/manifest.json` is the stable catalog. The canonical vector seed is
`tests/fixtures/ingest/roads.geojson`: two EPSG:4326 road LineStrings with canonical road
attributes. Integration tests materialize equivalent GeoPackage and Shapefile ZIP files from that
seed using the repository's installed GDAL stack. GeoTIFF is generated deterministically as a
10x10 EPSG:4326 UInt16 raster with nodata. OSM PBF reuses the committed
`tests/fixtures/osm/minimal.pbf`.

The matrix covers both happy and malformed input cases:

- GeoJSON: valid two-road source; malformed JSON.
- GeoPackage: valid `roads` layer; invalid non-SQLite bytes.
- Shapefile ZIP: valid `.shp/.shx/.dbf/.prj/.cpg` archive; ZIP with no `.shp`.
- GeoTIFF: valid CRS/nodata raster; invalid non-TIFF bytes.
- OSM PBF: valid committed minimal PBF; invalid non-PBF bytes.

GeoJSON, GeoPackage, Shapefile ZIP and GeoTIFF fixtures run through the real S03-T09 job service
and `DatasetIngestPipeline`, covering artifact storage, lifecycle transitions, inspection,
normalization and either canonical PostGIS persistence or normalized raster output. OSM PBF is
checked at the real T10 GDAL/Pyogrio reader boundary; the existing T12 integration test covers
PBF -> mapping -> canonical PostGIS persistence for all supported OSM source families.

The generated binary fixtures are derived from committed deterministic inputs instead of checked
in as large opaque blobs. This keeps review diffs small while still exercising real file formats
and real drivers in CI. Invalid cases must remain real malformed inputs rather than mocks.
