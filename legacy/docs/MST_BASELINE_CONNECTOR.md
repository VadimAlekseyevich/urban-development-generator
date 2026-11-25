# MST baseline connector — S06-T10

`MSTBaselineConnector` is the auxiliary baseline strategy that turns a bounded set of S06-T08 road anchors into a minimal deterministic connection topology and delegates geometry routing to S06-T09.

## Inputs and strategy contract

The strategy consumes:

- an immutable tuple of `CandidateRoadAnchor` values;
- the canonical `WeightedSuitabilityResult`;
- the matching `HardExclusionMask`;
- an optional `LeastCostConnectorPolicy` used by every selected anchor pair.

`AnchorConnectivityStrategy` is the pluggable core port. `MSTBaselineConnector` is its v1 baseline implementation. It does not depend on NetworkX, storage, API models or source-specific road objects.

## Topology selection

The MST is built from straight-line metric anchor distance in the common working CRS. Anchors are sorted by stable `anchor_id` first. Prim's algorithm then selects exactly `N-1` topology edges for `N >= 2` anchors with deterministic tie-breaking by distance, parent anchor id and target anchor id.

Implementation complexity is O(N²) time and O(N) auxiliary memory. `MSTBaselineConnectorPolicy.max_anchors` bounds the strategy independently and defaults to the S06-T08 maximum of 1,000 anchors.

The connector intentionally does **not** run S06-T09 for every possible anchor pair. Once the Euclidean MST topology is selected, only those `N-1` pairs are routed. This avoids an O(N²) set of bounded A* searches.

## Delegated least-cost routing

Every selected MST edge is routed through the existing S06-T09 pair connector. Therefore:

- hard-excluded and invalid-data cells remain impassable;
- suitability remains the soft cost surface;
- diagonal/corner-cut policy remains owned by `LeastCostConnectorPolicy`;
- each pair retains the S06-T09 `max_visited_cells` bound;
- pair outcomes stay `CONNECTED`, `NO_PATH`, or `SEARCH_LIMIT_REACHED`.

`AnchorConnectivityResult` preserves the planned topology edge, its direct metric distance and the full delegated least-cost result. Diagnostics aggregate connected/failed edge counts, visited cells, direct MST length, routed length and routed cost.

Empty and single-anchor inputs are trivially complete and require no path search.

## Failure semantics

A Euclidean MST edge can be unroutable because a hard mask separates its endpoints or because the bounded A* search reaches its limit. S06-T10 does not silently replace that edge with an all-pairs alternative search: the edge status is preserved and the overall result reports `complete=False`.

This is deliberate baseline behavior. A later strategy may use a richer candidate graph or recovery policy without changing the shared anchor-connectivity result contract.

## Scope boundary

S06-T10 chooses baseline anchor topology and routes the chosen pairs. It does not:

- mutate the existing/fixed `RoadGraph`;
- union overlapping generated geometries into a graph;
- add local/collector growth rules;
- classify generated roads;
- validate final road-network connectivity or dead-end ratios;
- persist `GeneratedRoad` rows.

Rule-based growth starts in S06-T11. Road classification and final road validation remain S06-T12/S06-T13.
