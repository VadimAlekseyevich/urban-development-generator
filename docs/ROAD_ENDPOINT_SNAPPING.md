# Road endpoint snapping integration

> **Status: Implemented**

S06-T03 provided the reusable `SpatialSnapIndex` primitive. M0 stabilization adds the missing
road-pipeline composition step: `EndpointRoadSnapper`.

## Contract

Input is an immutable tuple of canonical `SemanticRoad` values in one metric working CRS.

Only LineString/MultiLineString endpoints are candidates. Interior vertices are unchanged.

All endpoints are indexed once through `SpatialSnapIndex`. Endpoint pairs inside
`tolerance_m` form a tolerance graph. Each connected component snaps to the coordinate of the
lexicographically smallest stable endpoint id.

This clustering rule is required because independent nearest-neighbour replacement can produce
asymmetric A→B / B→A swaps instead of a common node.

## Bounds

Policy requires an explicit metric `tolerance_m` and caps:

- endpoint target count;
- unique candidate pairs discovered by indexed queries.

There is no all-pairs fallback.

## Failure policy

If snapping collapses a road part to zero length or makes it invalid, the operation fails
explicitly. It never silently drops source/fixed geometry.

## Position in the road pipeline

```text
resolved SemanticRoad inputs
 -> endpoint snapping
 -> semantic noding
 -> graph build
 -> cleanup
```

Generated roads are combined with fixed roads before the final noding/graph build so intersections
receive the same grade-separation semantics.

Fixed-network attachment for EXPANSION is a separate stabilization concern: endpoint snapping alone
does not guarantee that generated anchor topology touches the fixed network.
