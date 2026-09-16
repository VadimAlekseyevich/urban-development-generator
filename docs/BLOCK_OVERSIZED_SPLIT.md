# S07-T05 — Oversized block splitting

`core.urban_generator.blocks.oversized_split` implements only the bounded geometry subdivision required by S07-T05.

## Contract

`OversizedBlockSplitter` consumes the immutable S07-T04 `BlockFrontageValidationResult`, the S06 backend-independent `RoadGraph`, an explicit metric `working_srid`, and `OversizedBlockSplitPolicy(max_area_m2=...)`.

A fragment is oversized only when its polygon area is strictly greater than `max_area_m2`. Non-oversized blocks pass through unchanged except for deterministic post-T05 IDs and provenance fields.

For an oversized fragment the splitter chooses a deterministic reference direction:

1. query road-edge candidates with one `STRtree` against the fragment boundary;
2. if one or more candidate edges have positive-length boundary overlap, use the longest frontage edge, with stable edge-ID tie breaking (`ROAD_FRONTAGE`);
3. otherwise use the long side of the fragment minimum rotated rectangle (`PRINCIPAL_AXIS`).

The cut is perpendicular to that reference direction and passes through the polygon centroid when it lies in the polygon, otherwise through `representative_point()`. Successful children are normalized, deterministically ordered, and checked for area conservation.

Splitting is recursive until every fragment is within the area threshold or a configured bound prevents further subdivision. The implementation bounds:

- input blocks;
- road edges;
- road candidates per fragment;
- total split attempts;
- total output blocks;
- recursion depth.

If an oversized fragment cannot be split or reaches `max_split_depth`, it is retained and reported through `unsplittable_fragment_count`; it is never silently deleted.

`SplitBlockCandidate` keeps `input_block_id`, original `source_block_id` / `source_fragment_index`, and a deterministic `split_path`. T03 metrics and T04 frontage measurements are deliberately not copied because their geometry changed and those values would be stale.

## Explicit boundary

S07-T05 does **not** merge or drop small fragments, enforce a minimum output area, associate zones, create parcels, persist generated blocks, or add API/UI behavior. Sliver merge/drop policy and its diagnostics belong to S07-T06.
