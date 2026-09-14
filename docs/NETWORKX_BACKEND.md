# NetworkX backend adapter

`S06-T01` provides the v1 NetworkX implementation of the backend-independent
`NetworkBackend` domain port.

## Boundary

`NetworkXBackend` lives in `core.urban_generator.roads`, not in the domain package.
Domain code continues to depend only on:

- `NetworkGraphSnapshot`;
- `NetworkNodeRef`;
- `NetworkPoint` / `NetworkSnapResult`;
- `NetworkPath`;
- `NetworkDistanceResult`;
- `NetworkBackend`.

`networkx.Graph` is therefore an implementation detail of the v1 adapter, not the
road-domain contract.

## Adapter graph schema

The constructor accepts `Graph`, `DiGraph`, `MultiGraph`, or `MultiDiGraph`.
Every node id must be a non-empty string. By default nodes require finite metric
attributes:

- `x_m`;
- `y_m`.

Every edge requires finite non-negative `length_m`. Attribute names can be overridden
at adapter construction time. Extra node/edge/graph metadata is preserved so later road
semantics can travel through the adapter without becoming part of the S01 routing port.

The supplied graph is validated, copied into canonical node/edge insertion order, and
frozen. Mutating the caller's graph after adapter construction cannot mutate the served
snapshot. `to_networkx()` returns a detached mutable copy for adapter-level integrations.

## Snapshot and routing semantics

`NetworkXBackend.snapshot` converts backend state to immutable
`NetworkGraphSnapshot` metadata: snapshot id, metric working CRS, node/edge counts, and
directedness.

Routing uses `length_m` as the cost:

- `shortest_path` uses weighted Dijkstra and supports `max_distance_m` cutoff;
- `multi_source_distances` computes one bounded multi-source Dijkstra and returns the
  nearest reachable source for requested targets in target order;
- unreachable or cutoff-excluded targets are omitted / return `None` according to the
  existing S01 port contract.

Source ids are canonicalized before multi-source routing to keep equal-distance source
ties deterministic.

## Snapping

The backend builds one immutable Shapely `STRtree` over graph-node points. `snap()` uses
bounded `dwithin` lookup rather than scanning every node. Exact distances are computed
in the working CRS and equal-distance ties use canonical node id ordering.

This node-level routing snap is not the road geometry noding/snapping algorithm from
`S06-T03`; that later task handles line endpoints/intersections and configurable road
geometry tolerances.

## Scope exclusions

`S06-T01` intentionally does **not** implement:

- OSM bridge/tunnel/layer normalization (`S06-T02`);
- road line snapping or noding (`S06-T03` / `S06-T04`);
- road graph construction from source geometries (`S06-T05`);
- cleanup, road generation, persistence, API, or UI.

No city-specific assumptions are embedded in the adapter.
