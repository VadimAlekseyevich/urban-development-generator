# Suitability artifact

S04-T11 materializes the in-memory `WeightedSuitabilityResult` as a canonical, storage-neutral raster artifact. The writer lives in `core.urban_generator.suitability.artifact` and writes through the existing `ArtifactStore` port; callers never receive or depend on a filesystem path.

## Canonical GeoTIFF

The artifact is one two-band GeoTIFF in the exact `SuitabilityGridSpec` CRS, bounds, width, and height.

- **Band 1 — `suitability_score`**: `float32`, score in `0..1`. Invalid-data and hard-excluded cells remain `0`, matching the weighted-result contract.
- **Band 2 — `cell_status`**: `float32` representation of stable integer status codes: `0 = invalid data`, `1 = valid`, `2 = hard excluded`.

A separate status band is intentional. A score of zero can be a legitimate valid score, so nodata alone cannot distinguish an unsuitable valid cell from a cell that is missing required factor data or forbidden by a hard constraint.

The GeoTIFF is tiled and DEFLATE-compressed. Serialization walks the target grid in bounded windows. The source `WeightedSuitabilityResult` already owns full score/mask arrays, but the writer does not create an additional full-raster stack in memory.

## Statistics

`SuitabilityArtifactStatistics` is computed only from cells where `valid_mask == True`. It records:

- total, valid, hard-excluded, and invalid-data cell counts;
- configured minimum/preferred score thresholds;
- counts meeting the minimum and preferred thresholds;
- min, max, mean, p05, p50, and p95 score for valid cells.

When no valid cells exist, score distribution statistics are `None`; JSON metadata therefore never needs NaN/Infinity values.

## Provenance

`SuitabilityArtifactProvenance` records the information required to explain which suitability calculation produced the raster:

- artifact schema version (`suitability-artifact-v1`);
- suitability config version and SHA-256 fingerprint;
- positive-weight factor codes, implementation versions, configured weights, normalization policies, and normalization bounds;
- hard-exclusion source codes;
- working SRID and exact raster grid.

Statistics and provenance are both returned as typed immutable values and embedded into deterministic compact JSON GeoTIFF tags (`statistics_json`, `provenance_json`). The file also stores `schema_version` and `status_codes_json` tags.

## ArtifactStore lifecycle

`SuitabilityArtifactWriter.write()` accepts a temporary `ArtifactRef`. It writes the GeoTIFF to a private temporary file, streams that file to `ArtifactStore.put(..., content_type="image/tiff")`, and promotes the artifact to `ready`. If writing, storing, or promotion fails, temporary and ready refs are best-effort deleted so a failed calculation is not reported as a completed artifact.

The returned `SuitabilityArtifact.stat` is the ready `ArtifactStat`. Persisting the existing DB `artifacts` row or attaching the artifact to a generation stage remains an application-layer responsibility; S04-T11 does not introduce a new database schema.

## Validation and limits

The writer fails before storage writes when:

- result/config version or fingerprint disagree;
- the supplied hard mask uses another grid or different hard-excluded cells;
- positive-weight factor order does not match the weighted result provenance;
- the target grid exceeds `max_cells`;
- tile size is not a multiple of 16 between 16 and 4096.

The default cell budget is 25,000,000 and the default tile size is 512.

## Out of scope

S04-T11 does not expose the artifact through HTTP and does not render it in MapLibre. **S04-T12** owns the suitability layer API/UI, heatmap/raster visualization, statistics presentation, and factor metadata display.
