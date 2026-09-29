# S13-T02: bounded catalog vector GeoJSON / UUID keyset API

> Implementation: `backend/app/application/vector_layers.py`,
> `backend/app/db/vector_layer_query_repository.py`.
> Contract: `bounded-vector-v1` over the exact owner-qualified
> `LayerCatalog` definitions from S13-T01. Existing specialized GeoJSON
> endpoints remain backward compatible and continue powering today's UI.

## Request

```http
GET /api/v1/projects/{project_id}/vector-layers/{layer_id}/geojson
    ?dataset_version_id={uuid}&bbox=west,south,east,north
    &limit=1000&after={last-feature-uuid}&presentation_srid=4326&simplify_m=0
```

Use **exactly one** owner key according to the canonical catalog:
`dataset_version_id` for six `source.*` layers, or `run_id` for nine
run projections. Passing both, neither, or the wrong owner kind is invalid
(HTTP 422). The database adapter checks the project relationship before
any spatial read. An unknown catalog ID or a project-foreign/nonexistent
version/run yields HTTP 404. An absent published demographic or
infrastructure-demand read model is HTTP 409, never fallback geometry from
a different run. An owner can legitimately have zero in-bbox features.

The 15 supported UUID-table-backed definitions are:

- `source.landuse`, `source.water`, `source.constraints`,
  `source.facilities`, `source.buildings`, `source.roads`;
- `generated.zones`, `generated.demography`, `generated.blocks`,
  `generated.parcels`, `generated.buildings`, `generated.roads`;
- `run.existing_facilities`, `generated.facilities`,
  `generated.infrastructure_demand`.

Three other catalog definitions retain canonical dedicated read contracts
rather than a synthetic UUID row identity: `project.boundary` is a single
project feature, `analysis.suitability` is a georeferenced raster artifact
PNG + metadata, and `validation.violations` has a stable integer index in
the versioned persisted `ValidationReport`, not table UUIDs. The generic
UUID-keyset route returns HTTP 422 for them. Their existing APIs remain
authoritative; do not turn validation report indices into invented UUIDs.

## Bounds, projection and simplification

- `bbox` is required, 4 finite numbers in EPSG:4326, with
  `-180 <= west < east <= 180` and `-90 <= south < north <= 90`;
  antimeridian-crossing rectangles are explicitly rejected.
- `limit` defaults to 1,000; valid range is 1–5,000, further bounded by
  the selected catalog definition. The DB selects *at most limit+1*.
- `after` is the **exclusive** last feature UUID returned as `next_after`.
  SQL uses the existing `(dataset_version_id,id)` or `(run_id,id)`
  btree index and `ORDER BY id`, not OFFSET. When `truncated=false`,
  `next_after=null`, and the traversal is complete. The cursor is bound to
  the caller's current project, owner, layer, bbox and rendering policy by
  the request: clients must carry those values forward unchanged. Changing
  any of them starts a new traversal without `after`.
- `presentation_srid` must be exactly `4326` (RFC 7946 GeoJSON).
  WGS84 bbox is transformed into the run/project working CRS for GiST
  prefilter (`&&`) **and** `ST_Intersects`. GeoJSON geometry is transformed
  into EPSG:4326 for the response; no arbitrary output CRS is silently
  advertised.
- `simplify_m` defaults to 0 (no simplification), accepts finite
  0–100 metre tolerance, and requires a valid projected metre-unit
  `WorkingCRS` if non-zero. PostGIS `ST_SimplifyPreserveTopology`
  operates on the **rendered geometry** before transforming to WGS84, with
  a fallback to original if the simplification returns empty. The
  spatial membership predicate always uses the original geometry.
  This preserves source/run immutability and page membership, not exact
  vertex equality at nonzero tolerance. Geometry may extend beyond bbox:
  the query intersects the viewport, it does not clip geometries.
- This is a bounded **feature count** API, not a complete export, unlimited
  geometry byte-size guarantee, MVT, or raster format. Bounded binary MVT
  delivery is separately specified in S13-T03 (`docs/MVT_TILE_API.md`);
  immutable tile caching remains S13-T04.

`next_after` is a plain UUID rather than a signed encoded token: it does
not authorize access and cannot bypass project/owner scope checks; a random
UUID just moves the *same scoped query* start position. DatasetVersion
content and successful run records are immutable. Pages for still-active
runs can gain new features between requests; clients requiring an immutable
snapshot should use a successful run.

## Response

```json
{
  "schema_version": "bounded-vector-v1",
  "type": "FeatureCollection",
  "project_id": "…",
  "layer_id": "source.roads",
  "definition_version": "1",
  "dataset_version_id": "…",
  "run_id": null,
  "working_srid": 3857,
  "query_bbox": [-0.2, 51.45, 0.0, 51.6],
  "presentation_crs": "EPSG:4326",
  "simplify_m": 0.0,
  "limit": 1000,
  "truncated": false,
  "next_after": null,
  "features": []
}
```

Features preserve their canonical table IDs, typed source/generated
properties, source `attributes` and output geometries. The two generated
block JSON projections contain persisted demography/demand keys plus block
identities without recomputing population/accessibility. Existing facilities
are constrained to **exactly** the dataset versions linked to that run and
explicitly marked `origin=existing`.

## Verification / non-goals

`tests/unit/test_vector_layers.py` covers all 15 mapped definitions,
owner validation, pagination boundaries, missing readiness, projected
working CRS restrictions and invalid params. The PostGIS API integration
fixture in `tests/integration/test_vector_layer_api.py` checks full
project/version/run isolation, three-page keyset traversal, WGS84
projection, simplification without source mutation, status and typed
properties for run projections, and HTTP 404/409/422 failures.

No migration, generated read-model rewrite, frontend switch-over, stage\nDAG change or asynchronous export is part of UG-AI-082. Subsequent S13
tasks extend the same owner-qualified catalog rather than replacing it.
