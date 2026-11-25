# Vector inspection

S03-T04 adds a metadata-first vector datasource inspector for ingestion workers. It is intentionally separate from HTTP controllers, persistence, geometry repair and reprojection.

## Contract

`backend.app.services.VectorInspector` accepts a local vector datasource path and returns a `VectorDatasetInspection` with deterministic layer order. Each `VectorLayerInspection` contains:

- layer name and OGR driver;
- advertised geometry type;
- source CRS as reported by OGR;
- exact feature count;
- source-coordinate bbox `(xmin, ymin, xmax, ymax)` for non-empty spatial layers;
- detected text encoding where available;
- diagnostics showing whether feature count or bbox required a forced metadata pass.

The bbox is always in the layer's **source CRS**. S03-T04 does not reproject coordinates, repair invalid geometry, normalize geometry types or persist canonical features. Those concerns start in S03-T05 and later ingestion work items.

## Metadata-first behavior

The inspector uses Pyogrio/OGR rather than loading a GeoDataFrame. For every layer it first calls `read_info()` without forcing expensive calculations. Many drivers can answer feature count and total bounds directly from datasource metadata.

If the driver reports an unknown feature count (`-1`) or does not provide bounds for a non-empty spatial layer, the inspector performs at most one second `read_info()` call for that layer with only the missing `force_feature_count` / `force_total_bounds` flags enabled. The fallback may require OGR to scan features, but feature geometries and attributes are not materialized into Python memory.

`max_layers` defaults to 128 and is checked before per-layer inspection. This bounds the number of possible forced metadata passes to at most `2 * max_layers` metadata calls for one datasource. Datasources above the configured layer cap are rejected before any layer scans begin.

If exact feature count or the bbox of a non-empty spatial layer is still unavailable after the forced pass, inspection fails with `IncompleteVectorMetadataError` rather than silently returning ambiguous metadata. Empty spatial layers may legitimately have `bbox=None`.

## Shapefile ZIP boundary

S03-T03 remains responsible for safely extracting untrusted Shapefile ZIP archives. While inside `ShapefileZipExtractor.extract(...)`, callers can pass each returned `.shp` path to `VectorInspector`. The extracted directory is temporary and must not escape the extractor context.

S03-T04 itself does not unpack ZIP archives and does not trust archive member paths.
