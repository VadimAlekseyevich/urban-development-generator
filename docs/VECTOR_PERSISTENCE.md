# Batch vector persistence

`S03-T06` persists normalized vector batches from `S03-T05` into the canonical
PostGIS source tables created in `S02-T09`.

## Boundary

`SqlAlchemySourceLayerBatchWriter` owns one database transaction per `replace()`
call. The transaction contains:

1. DatasetVersion/project lookup and `working_srid` validation;
2. deletion of previously persisted rows for the same DatasetVersion and
   canonical layer;
3. bounded bulk INSERT statements;
4. optional post-load `ANALYZE`.

A failure in any later batch rolls the complete transaction back, including the
initial deletion. A retry therefore cannot leave a half-replaced canonical
layer.

Rows of a `DatasetVersion` whose status is already `ready` are rejected before
mutation. PostgreSQL triggers remain the final enforcement layer for both
DatasetVersion immutability and geometry SRID equality with
`Project.working_srid`.

## Canonical targets

The writer exposes the fixed `CanonicalSourceLayer` values:

- `roads`
- `buildings`
- `landuse`
- `water`
- `facilities`
- `constraints`

The corresponding ORM/PostGIS tables are fixed in code; table names never come
from request or dataset input.

The writer expects semantic canonical columns to have already been selected by
the ingestion orchestration. It does not implement OSM tag mapping or infer
domain classes from arbitrary source attributes. Those rules belong to later
ingestion tasks.

For schemas that require a multi geometry, persistence performs only the final
schema coercion:

- `LineString -> MultiLineString` for roads;
- `Polygon -> MultiPolygon` for buildings and landuse.

Other geometry repair, family filtering, and reprojection belong to
`S03-T05`.

## Attributes and source identity

Known canonical columns are written to typed table columns. Remaining source
columns are copied into `attributes_json`, together with an explicitly supplied
`attributes_json` mapping when present.

If `source_feature_id` is absent, a deterministic identifier is generated from
the source layer name and absolute feature position. This keeps retries stable
without depending on database-generated IDs.

## Bounded writes

`NormalizedVectorBatch` already bounds feature reads. Persistence adds an
independent `max_insert_rows` bound (default `5000`) and splits every input
batch into executemany INSERT chunks no larger than that value.

No complete layer is accumulated in memory by the writer.

## Index and statistics policy

The GiST geometry indexes and `(dataset_version_id, id)` BTREE indexes created
in `S02-T10` remain installed during ingestion. They are not dropped and rebuilt
per upload.

`PostLoadAnalyzePolicy` controls statistics refresh:

- `if_rows` (default): run `ANALYZE` after a non-empty load;
- `always`: run `ANALYZE` even after replacing the layer with zero rows;
- `never`: skip it.

This keeps the index lifecycle predictable while allowing tests or specialized
bulk workflows to disable post-load statistics refresh explicitly.
