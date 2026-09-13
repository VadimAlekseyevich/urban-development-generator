# Vector normalization

S03-T05 normalizes inspected vector layers into the project's metric working CRS without
loading an entire datasource into memory. It is a backend/worker capability; it does not
write canonical PostGIS tables. Batch persistence is S03-T06.

## Contract

`VectorNormalizer.iter_normalized_batches()` accepts:

- a datasource path/URI supported by Pyogrio;
- the exact `VectorLayerInspection` produced by S03-T04;
- a validated core `WorkingCRS`;
- a `VectorNormalizationConfig` with the expected geometry family and explicit policies.

The source CRS is mandatory and must be a usable horizontal geographic or projected CRS.
Every batch CRS is checked against the inspection result before any transformation. Output
is reprojected to `WorkingCRS`, which guarantees a projected CRS with metre horizontal units.
No metric calculation is performed in the source CRS.

## Bounded processing

The inspector's exact `feature_count` bounds the loop. Pyogrio reads at most `batch_size`
features per call (`5000` by default) with `skip_features` / `max_features`; a short
non-final batch, over-read, early EOF, or CRS change is a data-contract error. The iterator
returns one `GeoDataFrame` at a time so S03-T06 can persist and release each batch instead
of accumulating a full layer in RAM.

## Geometry policy

For every feature, normalization is deterministic:

1. null/empty input follows `EmptyGeometryPolicy` (`drop` by default, or `reject`);
2. invalid geometry is repaired with Shapely `make_valid`;
3. repaired `GeometryCollection` values keep only parts from the configured
   `GeometryFamily` (`point`, `line`, or `polygon`);
4. a geometry with no compatible part follows `GeometryMismatchPolicy` (`drop` by default,
   or `reject`);
5. the retained geometry is reprojected to the working CRS;
6. output must remain valid, non-empty, and have finite bounds.

Single and multi geometries both satisfy their family. Promotion to the exact canonical DB
geometry type (for example `LineString` -> `MultiLineString`) belongs to the S03-T06 writer,
which knows the target table contract.

## Diagnostics

Each `NormalizedVectorBatch` carries `VectorBatchDiagnostics` with input/output counts,
repaired geometries, dropped empty geometries, dropped type mismatches, and filtered
geometry collections. These counts are intentionally per-batch so the persistence/ingest
orchestration can aggregate them without retaining previous geometry batches.

## Non-goals

S03-T05 does not classify road/building/landuse attributes, write database rows, change
DatasetVersion state, enqueue jobs, or publish source-layer APIs. Those responsibilities
remain in later S03 work items.
