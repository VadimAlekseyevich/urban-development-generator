# S13-T03 — bounded project-scoped MVT tiles (UG-AI-083)

> Source: `backend/app/application/mvt_tiles.py`,
> `backend/app/db/mvt_tile_query_repository.py`.
> Binary `mvt-tile-v1` on the existing 18-definition `LayerCatalog`;
> S13-T04 adds state-qualified ETags/conditional responses without changing
> the canonical feature data or logical layer definitions.

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
| `Cache-Control` | `private, no-store` for active/unpublished input; `private, max-age=31536000, immutable` for truly published inputs |
| `ETag` | Strong quoted SHA-256 only for cache-immutable tile responses |

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
  S13-T04 advertises immutable caching only when the input state satisfies
  the terminal publication conditions below. Active output is never cached.

S13-T03 adds no migration, raster rendering, geospatial algorithm changes,
UI map-source registry, background export, or performance benchmark claim.
Existing GeoJSON endpoints and canonical validation codec remain intact.
Tests: unit limits/scope/oversize checks in
`tests/unit/test_mvt_tiles.py`; real PostGIS binary tile, layer properties,
source/version/run isolation, readiness, empty tile and error coverage in
`tests/integration/test_mvt_tile_api.py`.


## S13-T04: publication-qualified ETag and 304 semantics

The tile endpoint keeps identical URL, query bounds and binary MVT format.
It does **not** claim immutability from an owner UUID alone:

- A source tile qualifies only if the requested dataset version is **ready**.
  Migration `0023_published_tile_inputs` makes ready a terminal status; the
  existing source-row write triggers already protect the geometry/content.
- A generated/run-derived tile qualifies only for `status=succeeded`.
  The existing run/result/ref and generated-row DB triggers protect this
  state, not merely application-layer conventions.
- A `run.existing_facilities` tile qualifies only when the run succeeded
  **and every explicitly linked source version is ready**. If one linked
  source version is still processing, it remains `private, no-store` and has
  no ETag even if the run has already succeeded.
- Migration 0023 also protects a published project's `working_srid` and
  prevents moving a dataset with ready or succeeded-run-linked versions to
  another project. These protections remove coordinate/provenance loopholes
  that could otherwise make an advertised immutable URL change in place.
  Updating project CRS remains allowed before publication; later repairs
  must use a new project/version/run.

For eligible tiles, `Cache-Control: private, max-age=31536000, immutable`
is returned with a **strong quoted ETag** generated by SHA-256 over
`mvt-tile-v1`, layer definition version, the complete owner-qualified
catalog instance key, working SRID, XYZ, feature limit, bounded candidate
count and the complete encoded tile bytes. Empty tiles have an ETag too;
identical bytes from two different owners/layers/limits cannot share one.
`private` prevents a shared intermediary from storing a project-scoped
representation. Error responses (404/409/422/413) are `no-store`.

`If-None-Match` is evaluated **only after** scope/readiness checks and the
bounded SQL encoding/byte-budget gate. On matching exact/weak `W/`
validator, a comma-list containing the validator, or `*`, eligible tiles
respond `304 Not Modified` with no binary body, their ETag, cache headers
and the existing candidate/limit metadata. A mismatched validator returns
200. For volatile input, any `If-None-Match` value is ignored and the
response remains 200 with `private, no-store` and **no ETag**. A foreign
owner or malformed request can never bypass authorization via 304. This
design re-encodes a bounded tile to calculate a content-derived validator;
304 saves network transfer, not the initial bounded spatial query.

This is representation-level HTTP caching rather than a shared tile cache
or conditional query bypass. New incompatible render/serialization semantics
must bump `MVT_SCHEMA_VERSION`; callers must not mix ETags across the
owner, layer, tile XYZ, feature limit, or rendering contract. Revocation
of a previously downloaded private immutable representation is not
immediate: clients may keep their local copy during `max-age`. Do not use
this endpoint for access-controlled sensitive content without adding the
required authorization/cache invalidation policy.
