# S13-T03 — bounded project-scoped MVT tiles (UG-AI-083)

> Source: `backend/app/application/mvt_tiles.py`,
> `backend/app/db/mvt_tile_query_repository.py`.
> Binary `mvt-tile-v1` on the existing 18-definition `LayerCatalog`;
> no new canonical stage, table or dataset/run ownership model.

## Request and response

```http
GET /api/v1/projects/{project_id}/vector-layers/{layer_id}/tiles/{z}/{x}/{y}.mvt
    ?dataset_version_id={uuid}&feature_limit=500
GET /api/v1/projects/{project_id}/vector-layers/{layer_id}/tiles/{z}/{x}/{y}.mvt
    ?run_id={uuid}&feature_limit=500
```

The chosen definition must be one of the 15 **table-backed UUID-keyed**
entries already supported by `docs/BOUNDED_VECTOR_API.md`. Source layers
require `dataset_version_id` only; generated/run-existing layers require
`run_id` only. Caller-controlled layer IDs are resolved by canonical
`LayerCatalog` and then by a fixed server-side table/column allowlist.
The repository verifies the requested project owns the exact version/run
before reading geometry; the run-existing facility projection joins only
dataset versions explicitly linked to that run. Missing/unowned owner or
unknown layer gives HTTP 404; incompatible/missing scope gives 422. Derived
`generated.demography` and `generated.infrastructure_demand` require
their published canonical metrics/read model or return HTTP 409.

`project.boundary` is a single-feature route, `analysis.suitability` a
raster with its own georeferencing, and `validation.violations` a canonical
JSON report with integer indices (no physical UUID-keyed geometry rows).
These three **do not** invent synthetic UUIDs or false tile availability;
they retain existing specialist APIs and reject the generic MVT route with
422.

On success the response body is binary
`application/vnd.mapbox-vector-tile`, including a zero-byte valid empty
tile. The MVT internal layer name is exactly the canonical `layer_id`;
`feature_id` is the persisted UUID as a **string property**, not an
incorrect 64-bit MVT integer feature ID. The tile includes typed scalar
source/generated fields and supported flattened scalar JSONB properties;
nested arrays/objects are not promised as MVT properties. The GeoJSON
view remains authoritative for complete nested/read-model properties.
Header metadata:

| Header | Contract |
|---|---|
| `X-MVT-Schema-Version` | `mvt-tile-v1` |
| `X-MVT-Feature-Limit` | selected 1–1000 |
| `X-MVT-Candidates` | pre-encoding intersecting candidates, at most N+1 |
| `X-Features-Truncated` | true if candidate count > N; it is **not** the exact post-clipping feature count |
| `Cache-Control` | `no-store` until S13-T04 immutable ETag/cache rules |

## Bounded spatial work and tile projection

- Strict XYZ: integer zoom **0–16**, integer `0 <= x,y < 2^z`.
  No unbounded arbitrary tile envelopes or client-supplied SQL/CRS.
- `feature_limit` defaults to **500**, maximum **1000**.
  The SQL materializes a candidate CTE limited to `N+1`, in stable
  owner+UUID order. A second query from that **same** CTE selects at most
  `N` for encoding. There is no OFFSET, full-layer MVT aggregation,
  full-table count, or unlimited feature scan/geometry encoding.
- `ST_TileEnvelope(z,x,y)` defines EPSG:3857 tile boundaries.
  A 64/4096 margin expands the envelope, then transforms to the exact
  persisted metric/project working SRID. The raw working geometry is
  tested with GiST `&&` **and** `ST_Intersects`, not by unindexed
  `ST_Transform(geometry)` in the filter.
- Only bounded selected features undergo
  `ST_AsMVTGeom(ST_Transform(geometry,3857), tile_envelope,4096,64,true)`
  followed by `ST_AsMVT`. Tile clipping/buffer affects presentation
  only; stored source/generated geometries and owner provenance never
  change. A clipped degenerate object can be omitted from the encoded
  tile even if it was selected as a candidate.
- Database statement work has a **5-second transaction-local** timeout;
  output larger than **1 MiB** is rejected with HTTP **413**, never served
  as a silently truncated/corrupt tile. This is a deliberately bounded
  interactive tile contract, not an exhaustive vector export.
- Successful generation runs and immutable dataset versions support
  reproducible geometry inputs; queued/running run output can change.
  The endpoint advertises **no immutable ETag or long-lived caching yet**:
  cache policy for fully published, versioned tiles belongs to S13-T04.

S13-T03 adds no migration, raster rendering, geospatial algorithm changes,
UI map-source registry, background export, or performance benchmark claim.
Existing GeoJSON endpoints and canonical validation codec remain intact.
Tests: unit limits/scope/oversize checks in
`tests/unit/test_mvt_tiles.py`; real PostGIS binary tile, layer properties,
source/version/run isolation, readiness, empty tile and error coverage in
`tests/integration/test_mvt_tile_api.py`.
