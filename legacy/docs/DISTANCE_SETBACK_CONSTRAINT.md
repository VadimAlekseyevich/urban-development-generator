# Distance/setback constraint

S04-T03 adds reusable metric setback checks on top of the S04-T01 registry/engine.
It remains a pure `core` capability with no FastAPI, SQLAlchemy, Redis, worker, or React dependency.

## Contract

`DistanceSetbackConstraint` is a HARD constraint. A `DistanceSetbackSubject` carries a valid,
non-empty Shapely geometry plus the project `working_srid`.

The reusable `DistanceSetbackIndex` is configured with immutable `DistanceSetbackBand` values.
Each band contains:

- a kind: `ROAD`, `BUILDING`, or generic `FEATURE`;
- a minimum clearance in metres;
- an immutable tuple of source geometries already expressed in the same working CRS.

A candidate passes when its minimum Euclidean distance to every active band feature is greater
than or equal to the configured setback. Intersection/touching gives distance `0` and therefore
fails every positive setback. A zero-distance band is accepted as an explicit no-op.

## CRS and units

No distance calculation is permitted in EPSG:4326 or another non-metric CRS. Construction of the
subject/index validates the SRID through the existing `require_working_crs()` guard, and constraint
evaluation additionally requires the `TerritorySnapshot` and `RunContext` SRIDs to match the
index SRID.

Reprojection is intentionally out of scope; ingest/stage preparation must provide geometries in
the project working CRS.

## Spatial indexing and bounded work

Each active setback band owns one reusable Shapely `STRtree`. For every candidate, the rule first
queries the tree with the candidate bounds expanded by that band's setback distance, then runs the
exact Shapely `distance()` predicate only for returned candidates.

`max_candidates` (default `10_000`) bounds the total number of tree candidates examined during one
check. Overflow raises `DistanceSetbackCandidateLimitError` and fails closed instead of degrading
to an unbounded spatial scan.

Diagnostics are deterministic: bands are evaluated in configuration order, and features inside a
band are evaluated in their original tuple order.

## Result semantics

The rule emits the existing immutable `ConstraintResult` contract. On failure, the message records
feature kind/index, band index, actual metric distance, and required minimum distance.

## Out of scope

S04-T03 deliberately does not add raster threshold logic (S04-T04), suitability factors/masks,
database queries, HTTP endpoints, reprojection, or persistence of violation geometries.
