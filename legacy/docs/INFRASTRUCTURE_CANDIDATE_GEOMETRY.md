# Infrastructure candidate site geometry

S10-T05 removes point-only generated-facility candidates.

## Spatial representation

Every T04 candidate becomes exactly one of:

- `SITE`: a Polygon/MultiPolygon site for block/parcel sources;
- `HOST_BUILDING`: an explicit `host_building_id` for building sources.

A building-host candidate never duplicates a separate site geometry.

## Polygon site construction

For block and parcel sources the target area is
`InfrastructureType.target_site_area_m2`.

- if source area is at or below target area, the full source geometry is used;
- otherwise a deterministic bounded square-window search around the T04 anchor intersects
  the source until the polygonal result converges to target area.

The generated site is valid, 2D, positive-area, contains the candidate anchor, and remains
inside the source geometry.

## Provenance

Candidate id, infrastructure type, source kind/id, zone class, working SRID and optional
zone/block ids are preserved exactly from T04.

## Scope boundary

T05 guarantees a site/footprint or explicit host building. It does not yet decide whether
the geometry satisfies minimum-site/capacity feasibility (T09), and it does not perform
road-network snapping or accessibility (T06/T07).
