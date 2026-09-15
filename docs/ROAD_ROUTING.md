# Road routing contract

## S06-T07 — Shortest-path services

Routing consumes an already built and cleaned metric road graph. It does not perform snapping,
semantic noding, graph cleanup, candidate-anchor sampling, or road generation.

The backend-independent `NetworkBackend` exposes three routing operations:

- `shortest_path(source, target, algorithm=..., max_distance_m=...)`;
- `multi_source_shortest_path(sources, target, algorithm=..., max_distance_m=...)`;
- `multi_source_distances(sources, targets, max_distance_m=...)`.

All public results remain domain values: `NetworkPath` and `NetworkDistanceResult`. Callers do not
receive NetworkX paths, exceptions, graph objects, or node dictionaries.

`NetworkRoutingAlgorithm` supports `dijkstra` and `astar`. Dijkstra remains the default so existing
callers preserve their behaviour. Multi-source distance batches use a single Dijkstra traversal;
A* is available for single-target queries, including nearest-source-to-target routing.

## A* heuristic

Node coordinates are measured in the validated project `WorkingCRS`. A* uses Euclidean node-to-
target distance multiplied by a graph-wide scale factor. The factor is the minimum of `1.0` and
`edge.length_m / endpoint_euclidean_distance` over non-zero-span edges.

This keeps the heuristic admissible even for an externally supplied metric graph whose edge weight
is shorter than the straight-line distance implied by its node coordinates. A zero-cost edge over
non-zero coordinate distance reduces the scale to zero, making A* safely degenerate to Dijkstra.

## Determinism and bounds

Routing never depends on NetworkX insertion order for equal-cost choices:

- source ids are deduplicated and sorted;
- neighbour ids are traversed in sorted order;
- equal-distance multi-source choices prefer the lexicographically smaller source id;
- public target results keep the caller's target order.

Every routing query is bounded by `max_routing_visited_nodes` (default `1_000_000`). Exceeding the
bound raises `NetworkXBackendError` instead of allowing an unbounded traversal. Optional
`max_distance_m` is a metric path-cost cutoff and filters candidate relaxations beyond that value.

Parallel edges remain supported through the `MultiGraph` adapter. For topology routing between a
node pair, the lowest `length_m` parallel edge is the effective traversal cost; S06-T07 does not
remove or merge those edges.

Directed NetworkX graphs are respected by the adapter, but the S06-T05 `RoadGraph` adapter remains
an undirected `MultiGraph` under the current v1 pedestrian-accessibility semantics.

## Scope boundary

S06-T07 does not add candidate road anchors, least-cost raster connectors, MST strategies, or road
growth. Candidate sampling begins in S06-T08.
