# Road metrics — S06-T14

S06-T14 calculates deterministic raw metrics from the metric-CRS `RoadGraph` and an explicit positive analysis area in square metres. It does not apply a composite score, mutate the graph, persist generated roads, or perform S06-T13 acceptance validation.

The v1 metric set contains total and generated road length, road-length density, connected-component count, mean/max node degree, configurable intersection count and density, and edge-weighted circuity.

`length_density_km_per_km2` is total network kilometres divided by analysis-area square kilometres. `intersection_density_per_km2` uses nodes whose degree is at least `intersection_min_degree` (default 3).

For v1, `edge_weighted_circuity` is the sum of eligible edge geometry lengths divided by the sum of straight endpoint chord lengths. Closed-loop edges with coincident endpoints are excluded from that denominator and counted separately through `circuity_edge_count`; if no edge has a positive endpoint chord, circuity is `None`. This is an explainable geometry/network-form metric, not a replacement for later OD/network-route accessibility metrics.

Calculation is O(V+E) and guarded by explicit node/edge limits. Persistence of `GeneratedRoad` remains S06-T15.
