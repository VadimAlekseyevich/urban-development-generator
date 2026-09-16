# S07-T06 — Sliver cleanup

`core.urban_generator.blocks.sliver_cleanup` implements the explicit merge/drop cleanup policy for post-S07-T05 block fragments.

## Contract

`BlockSliverCleaner` consumes an immutable `OversizedBlockSplitResult` in the explicit project working CRS plus `SliverCleanupPolicy`.

A block/group is a sliver when its current polygon area is strictly less than `min_area_m2`.

The cleaner builds one `STRtree` over T05 output and materializes a bounded adjacency graph from positive shared boundary length. Positive-area overlaps are rejected rather than silently repaired. The graph is contracted incrementally after successful merges, so cleanup does not repeatedly perform a full spatial all-pairs scan.

### Merge policy

By default `merge_within_input_block_only=True`. This means a sliver can merge only with another T05 fragment originating from the same T04 `input_block_id`. The default prevents cleanup from dissolving a road boundary merely because polygons on opposite sides share that road line as their boundary.

When several merge targets are available, selection is deterministic:

1. longest shared boundary;
2. larger target area;
3. lexicographically stable T05 member IDs.

Merged geometry must remain one valid 2D `Polygon` and must conserve the combined source area within tolerance. `CleanedBlockCandidate.members` preserves every T05 member object so merged provenance remains explicit.

Cross-input merges require the explicit opt-in `merge_within_input_block_only=False`.

### Unmergeable policy

`UnmergeableSliverAction.KEEP` is the safe default. The sliver remains in output and emits a `KEPT_UNRESOLVED` event.

`UnmergeableSliverAction.DROP` is explicit destructive policy. A dropped sliver emits a `DROPPED` event containing its member IDs and exact area. `SliverCleanupDiagnostics.dropped_area_m2` records the aggregate removed area.

Every cleanup operation emits a `SliverCleanupEvent`; there is no silent merge or deletion.

## Area accounting

The result enforces:

```text
input_area_m2 == output_area_m2 + dropped_area_m2
```

within the documented floating-point tolerance. This invariant is validated both while merging and on final output.

## Bounded work

The cleaner bounds:

- input block count;
- spatial candidates per block;
- eligible adjacency pair count;
- total merge/drop/keep operations.

## Explicit boundary

S07-T06 does **not** associate zones, create parcels, persist generated blocks, expose API endpoints, or add UI behavior. Explicit block-to-zone association belongs to S07-T07.
