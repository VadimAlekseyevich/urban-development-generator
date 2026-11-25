# Base zoning partition geometry

S05-T04 introduces an infrastructure-independent geometry stage between deterministic zoning seeds and later zone-class assignment.

## Contract

`BaseZoningPartitioner.partition(...)` accepts:

- a deterministic `ZoningSeedSet` from S05-T03;
- a polygonal developable area (`Polygon` or `MultiPolygon`) in project coordinates;
- the metric project `working_srid`.

The output is a `ZoningPartitionResult` containing one `ZoningPartitionCell` per seed. Cells stay ordered by `seed_index`, and each cell retains the exact generating `ZoningSeed` for the later assignment stage.

## Geometry algorithm

For two or more seeds the partitioner builds a Voronoi diagram from seed coordinates. Raw Voronoi cells are mapped back to their generating seeds spatially rather than relying on GEOS output order. Each mapped cell is intersected with the developable area.

For one seed, the whole developable area is the single partition cell.

The developable area can be concave, contain holes, or be multipart. Clipping therefore may produce `MultiPolygon` cells. This is intentional: S05-T04 represents geometric ownership only and does not split one seed into artificial zone classes.

## Validity repair

An invalid polygonal developable input is passed through Shapely/GEOS `make_valid`. If repair returns a geometry collection, only polygonal components are retained. Invalid clipped cells are repaired the same way.

The result contract never exposes empty or invalid partition cells. Diagnostics record whether the developable area was repaired and how many clipped cells required validity repair.

## Partition invariants

Before returning, the partitioner checks in metric area units that:

- every seed is covered by the repaired developable area;
- seed coordinates are unique;
- every seed maps to exactly one raw Voronoi cell;
- every output cell covers its generating seed;
- the union of output cells covers the developable area within a small numeric tolerance;
- output cells do not extend outside the developable area by positive area beyond tolerance;
- output cells do not overlap by positive area beyond tolerance.

Shared boundaries are expected and do not count as overlap.

## Deliberate scope boundary

S05-T04 does **not** assign `residential`, `mixed`, `public`, or `recreation` classes. Suitability and target-share based class assignment belongs to S05-T05. Region growth/refinement, constraint evaluation, persistence, API, and UI remain later roadmap items.

The implementation contains no city-specific geometry assumptions. Ryazan can be used later as a reference dataset without changing this core contract.
