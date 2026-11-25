# S07-T04 — Block frontage/access validation

`core.urban_generator.blocks.BlockFrontageValidator` validates road access and positive-length frontage for the measured developable blocks produced by S07-T03.

## Inputs

The validator consumes:

- immutable `BlockMetricsResult` from S07-T03;
- backend-independent S06 `RoadGraph`;
- one explicit projected working CRS shared by both inputs;
- `BlockFrontagePolicy` with `access_tolerance_m` and `minimum_frontage_m`.

Input blocks, road edges and per-block spatial candidates are explicitly bounded.

## Indexed access query

Road geometries are placed in one Shapely `STRtree`.

For each block boundary:

1. `STRtree.nearest` finds the nearest road edge without scanning all edges;
2. an equal-distance `dwithin` query resolves nearest-edge ties deterministically by exact distance and stable `edge_id`;
3. `has_access` is true when nearest boundary-to-road distance is within `access_tolerance_m`.

An empty road graph returns no nearest edge and no access.

## Frontage

Frontage candidates are discovered by the same `STRtree`, then exact intersections are calculated between the block boundary and candidate road lines.

Only **positive-length** line overlap counts as frontage. Point contact or a transverse crossing has zero frontage. This is intentional: a purely geometric crossing, including a possible grade-separated crossing whose topology does not provide a block edge, must not become valid frontage merely because two geometries touch at a point.

Overlapping/parallel road edges do not double-count frontage length: positive-length overlap geometries are unioned before their length is measured. `frontage_road_ids` still records every road contributing positive overlap.

Validation requires both:

- road access within the configured tolerance;
- positive frontage meeting `minimum_frontage_m`.

With the default minimum of `0`, a block still needs strictly positive frontage; zero-length point contact never passes.

## Explicit work-item boundary

S07-T04 does **not** implement:

- oversized block thresholds or splitting (`S07-T05`);
- sliver merge/drop policy (`S07-T06`);
- zone association, parcel subdivision, persistence, API or UI.
