# Infrastructure network snapping

S10-T06 snaps infrastructure demand and facility sites to one immutable road-network
snapshot through the backend-independent `NetworkBackend` port.

## Reusable nearest index

The infrastructure layer calls `NetworkBackend.snap()`; it does not implement a second
nearest-neighbour structure. The NetworkX adapter already owns a reusable STRtree-backed
`SpatialSnapIndex` for graph nodes.

## Demand subjects

T03 produces demand per block/type, but network location is block-level. T06 therefore
deduplicates all demand rows to one snap per unique block and derives its point from the
authoritative zone-associated block geometry.

## Site subjects

Two site classes are snapped:

- S10-T05 generated candidate sites/host-building anchors;
- S10-T02 existing facilities, using point geometry directly or a representative point.

Each snapped site retains its infrastructure type and whether it is generated candidate or
existing infrastructure.

## Bounds and failures

All input spatial objects must use the network snapshot working SRID. A dedicated
`max_snap_distance_m` controls only node snapping and is not the same as an
InfrastructureType service-distance cutoff.

Subjects without a node inside the snap radius are returned as explicit unsnapped demand
or site records. A global subject-count budget prevents unbounded work.

## Scope boundary

T06 only maps subjects to graph nodes. S10-T07 performs network-distance accessibility and
applies per-type max service distance.
