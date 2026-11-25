# Geometry exclusion constraint

S04-T02 adds the first concrete spatial rule on top of the S04-T01 registry/engine.
It stays inside `core` and has no FastAPI, SQLAlchemy, Redis, worker, or React dependency.

## Contract

`GeometryExclusionConstraint` is a HARD constraint. It accepts a
`GeometryExclusionSubject` containing:

- a non-empty valid Shapely geometry;
- the project `working_srid` explicitly.

The rule is configured with a reusable `GeometryExclusionIndex` built from:

- one polygonal project boundary;
- zero or more water geometries;
- zero or more protected-area geometries;
- the same project `working_srid`;
- a bounded `max_candidates` policy.

The rule can be registered for any appropriate `ConstraintScope`. Stage-specific decisions
remain in registry composition, not inside the geometry algorithm.

## Spatial semantics

A candidate passes only when both conditions hold:

1. the project boundary `covers` the entire candidate;
2. the candidate does not `intersect` any indexed water or protected geometry.

`covers` deliberately allows a candidate to touch the project boundary. Water/protected
intersection is stricter: touching an exclusion also fails the rule.

When several exclusions match, diagnostics are deterministic: water precedes protected areas,
then the original geometry order is used within the category.

## CRS contract

All geometries are assumed to already be expressed in the project working CRS. The index,
subject, `TerritorySnapshot`, and `RunContext` must expose the same metric EPSG SRID. A mismatch
raises `GeometryExclusionError` instead of performing a spatial predicate on incompatible
coordinates.

S04-T02 does not perform reprojection. Reprojection belongs to ingest/stage preparation.

## Prepared/index reuse

`GeometryExclusionIndex` is intentionally reusable across many candidates in one stage:

- the boundary is prepared once with GEOS prepared geometry;
- water and protected geometries share one Shapely `STRtree`;
- each candidate first uses the tree's bounding-box query and then exact prepared
  intersection predicates only for returned candidates.

This avoids rebuilding spatial structures in a candidate loop.

The tree query is guarded by `max_candidates` (default `10_000`). If the envelope query returns
more entries, evaluation fails closed with `GeometryExclusionCandidateLimitError` rather than
falling back to an unbounded scan.

## Result semantics

The rule emits the existing immutable `ConstraintResult` contract:

- inside boundary and no exclusions -> `passed=True`;
- outside/crossing boundary -> HARD failure;
- touching/intersecting water -> HARD failure;
- touching/intersecting protected geometry -> HARD failure.

No violation geometry persistence is introduced here. Later validation/persistence work items
can preserve detailed geometries without changing this rule's evaluation contract.

## Out of scope

S04-T02 intentionally does not add:

- distance or setback calculations (S04-T03);
- raster threshold logic (S04-T04);
- suitability masks or scoring (S04-T06+);
- database queries or HTTP endpoints.
