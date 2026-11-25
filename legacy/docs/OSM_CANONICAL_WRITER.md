# OSM canonical writer

S03-T12 connects the bounded OSM reader/mapping contracts from S03-T10/S03-T11 to the canonical source-layer persistence created in S03-T06.

## Contract

`OsmCanonicalWriter.replace_layer(...)` persists exactly one `OsmMappedLayer` for one immutable `DatasetVersion` attempt. The caller supplies:

- the `dataset_version_id`;
- one explicit target layer (`roads`, `buildings`, `facilities`, `landuse`, or `water`);
- the project's validated metric `WorkingCRS`;
- the mapping ruleset version used to produce the input;
- a bounded iterable of `MappedOsmBatch` values.

Every batch and feature must target the requested layer and carry the same mapping version. Input batches must remain in OSM WGS84 (`EPSG:4326`). The writer preserves `source_feature_id`, canonical attributes, raw OSM-tag provenance, mapping version/rule metadata, and geometry.

## Bounded reprojection and persistence

The writer does not collect a complete PBF layer in memory. Each non-empty mapped batch becomes one GeoDataFrame, is reprojected to the project working CRS, checked for non-empty/valid/finite geometry, and is yielded immediately as a `NormalizedVectorBatch` to `SqlAlchemySourceLayerBatchWriter`.

The S03-T06 writer remains the only SQL implementation. It owns the layer replace transaction, bounded bulk inserts, canonical MULTI coercion, SRID validation against the project, uniqueness, rollback, and post-load `ANALYZE` policy. Therefore an OSM retry replaces the previous rows for that dataset-version/layer rather than appending duplicates. An empty input is a valid replacement and clears that layer.

A PBF containing several source families is intentionally written one canonical target layer at a time. This preserves streaming behavior and avoids silently buffering the entire PBF just to regroup categories. Multi-layer orchestration belongs to the ingest pipeline/worker composition, not to this persistence adapter.

## Mapping provenance

T12 does not classify tags. It consumes the already mapped T11 contract and rejects mapping-version drift. The canonical `attributes_json` produced by T11 contains the original relevant OSM tags, OSM element identity, `osm_mapping_version`, and `osm_mapping_rule`, so persisted rows remain auditable after normalization.

## Non-goals

S03-T12 does not add HTTP endpoints, source-layer visualization, new database tables, migrations, or new mapping rules. Source-layer bbox access is S03-T13 and UI visualization is S03-T14.
