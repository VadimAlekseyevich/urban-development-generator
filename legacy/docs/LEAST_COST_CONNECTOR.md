# Least-cost road connector — S06-T09

`LeastCostConnector` is the bounded raster-routing step that turns two S06-T08 `CandidateRoadAnchor` values into one proposed connection geometry. It does not select which anchor pairs should be connected and does not mutate the fixed/source road graph.

## Inputs and CRS

The connector consumes:

- two distinct `CandidateRoadAnchor` values;
- the canonical `WeightedSuitabilityResult`;
- the matching canonical `HardExclusionMask`;
- an explicit `LeastCostConnectorPolicy`.

The suitability grid is the metric spatial reference for the search. Its `working_srid` must be a projected metre-based CRS through the existing CRS guard. The hard mask must use exactly the same `SuitabilityGridSpec`, and its boolean cells must exactly match `WeightedSuitabilityResult.hard_excluded_mask`. Anchor row/column, point coordinates and suitability score are checked against the current raster so stale anchors are rejected instead of silently routed on a different cost surface.

## Cost surface

A* runs over valid raster cells. Hard-excluded cells are never traversable. Cells with invalid suitability data are also non-traversable because their soft traversal cost is unknown.

For one move, geometric distance is computed from the metric raster cell width/height. Soft suitability changes that distance by a configurable multiplier:

```text
cell_factor = 1 + suitability_penalty_weight * (1 - suitability_score)
step_cost = step_distance_m * mean(current_factor, neighbor_factor)
```

The default `suitability_penalty_weight` is `1.0`; setting it to `0.0` reduces the search to pure geometric shortest path over the same traversable mask. The A* heuristic is the straight-line metric distance to the target. Because every traversal factor is at least `1.0`, the heuristic remains admissible.

## Neighborhood and hard-mask safety

By default the connector uses an 8-neighbor grid. Diagonal steps are deterministic and allowed only when both adjacent cardinal cells are also traversable, preventing a path from cutting through the corner of hard-excluded or invalid cells. `allow_diagonal` and `prevent_corner_cutting` are explicit policy options.

## Bounded work and result states

`max_visited_cells` bounds A* expansion and defaults to 250,000 cells. The result has a stable status:

- `CONNECTED` — contains metric `LineString`, raster-cell path, length and total cost;
- `NO_PATH` — the traversable search frontier was exhausted;
- `SEARCH_LIMIT_REACHED` — the explicit visit bound stopped the search before a conclusion.

`NO_PATH` and `SEARCH_LIMIT_REACHED` deliberately remain distinct so later orchestration can decide whether to reject a pair, retry with a different policy, or choose another strategy without parsing exception strings. Hard-exclusion source codes are propagated into the result for provenance.

## Determinism

Frontier ordering includes estimated cost, accumulated cost, raster row and column. Equal-cost parent updates use a stable raster-cell tie-break. Given identical anchors, raster inputs and policy, the same raster path and geometry are returned.

## Scope boundary

S06-T09 routes exactly one requested anchor pair. It does not choose a global connection set, construct an MST, grow local/collector roads, classify generated roads, persist `GeneratedRoad`, or update the existing `RoadGraph`. Pair selection and baseline graph connectivity remain S06-T10; rule-based growth remains S06-T11.
