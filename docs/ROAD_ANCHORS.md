# Candidate road anchors — S06-T08

`CandidateRoadAnchorSampler` is the bounded pre-connector step between zoning/suitability and generated-road connection strategies.

## Inputs

The sampler consumes existing core contracts only:

- `ZoningPartitionResult` for metric zone geometries;
- `ZoneAssignmentResult` for the current functional class of each partition cell (including a refined assignment when S05 refinement has run);
- `WeightedSuitabilityResult` for the canonical `0..1` score and validity/hard-exclusion masks;
- `CandidateRoadAnchorPolicy` for explicit sampling limits and filters.

All inputs must use the same projected metre-based working CRS. The sampler does not read DB rows, source-specific OSM tags or NetworkX objects.

## Policy

`CandidateRoadAnchorPolicy` has no implicit zone preference. By default all canonical `ZoneClass` values are eligible and the minimum suitability score is `0.0`. Callers may explicitly restrict the allowed zone classes or raise the score threshold.

Work is bounded independently in two places:

- `max_sampled_cells` defaults to 250,000;
- `max_candidates` defaults to 1,000.

When the suitability grid exceeds the sampling budget, the sampler chooses deterministic evenly distributed row/column indexes whose Cartesian product does not exceed `max_sampled_cells`. This avoids an unbounded full-raster pass while retaining two-dimensional spatial coverage.

## Spatial lookup and ranking

Only sampled cells with `valid_mask=True` are considered, so hard-excluded and invalid-data cells cannot become anchors. Each sampled raster-cell center is located in the zoning partition through one `STRtree` query; there is no raster-cells × zoning-polygons nested scan.

A point covered by more than one partition cell only because it lies exactly on a shared boundary uses the smallest stable partition cell index. Candidate metadata preserves:

- deterministic raster-derived `anchor_id`;
- metric `NetworkPoint`;
- zoning cell index and `ZoneClass`;
- raster row/column;
- suitability score.

Eligible candidates are ranked deterministically by descending suitability score and then stable zoning/raster tie-breaks. Only the first `max_candidates` are returned.

Diagnostics report the full grid size, sampled row/column/cell counts, valid sampled cells, eligible/selected candidate counts, whether either bound was active, and eligible/selected counts for every canonical zone class. Zoning and suitability config fingerprints are propagated into the result for provenance.

## Scope boundary

S06-T08 only proposes anchor candidates. It does not snap anchors to the road graph, create road geometry, run cost-surface A*, choose MST edges or mutate the fixed source network. Least-cost connection with hard masks is S06-T09.
