# Source layer GeoJSON viewport API

S03-T13 exposes canonical vector source layers for map inspection without putting
PostGIS query details in FastAPI controllers.

## Endpoint

`GET /api/v1/projects/{project_id}/dataset-versions/{dataset_version_id}/source-layers/{layer}/geojson`

Supported `layer` values are `roads`, `buildings`, `landuse`, `water`,
`facilities`, and `constraints`.

The request requires:

- `bbox=west,south,east,north`
- `limit`, default `1000`, maximum `5000`

`bbox` is always EPSG:4326 longitude/latitude. Coordinates must be finite,
`west < east`, and `south < north`; antimeridian-crossing boxes are intentionally
out of scope for this bounded viewport endpoint.

## CRS contract

Canonical geometries remain stored in each project's metric `working_srid`.
The repository transforms the constant WGS84 request envelope into that working
CRS, filters there, and transforms only returned geometries to EPSG:4326 for
GeoJSON serialization.

This keeps metric source data authoritative while making the HTTP contract
directly consumable by web maps. The response includes both `geojson_crs` and
`working_srid` explicitly.

## Spatial and limit policy

The PostGIS adapter scopes every query by `dataset_version_id`, verifies that
the version belongs to the project in the URL, and uses the existing GiST
geometry index through an envelope overlap predicate plus exact
`ST_Intersects`.

The application service requests `limit + 1` rows. It returns at most `limit`
features and sets `truncated=true` when more matching rows exist. T13 does not
introduce unbounded reads or cursor pagination; map clients should narrow the
viewport when a response is truncated.

Rows are ordered by canonical feature UUID for deterministic bounded reads.

## GeoJSON feature properties

Each feature contains:

- canonical feature UUID as GeoJSON `id`;
- `source_feature_id`;
- the layer's typed canonical columns, such as `road_class` or
  `building_class`;
- original normalized extra attributes under `properties.attributes`.

No migration is required for S03-T13. It reads the canonical tables and spatial
indexes introduced in S02/S03.
