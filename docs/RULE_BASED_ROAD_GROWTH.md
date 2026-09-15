# Rule-based road growth — S06-T11

`RuleBasedRoadGrower` is the bounded post-MST densification step. It consumes the S06-T08 anchors, an S06-T10 `AnchorConnectivityResult`, and the same suitability/hard-mask surface used by pair routing. It proposes additional anchor-to-anchor connections without mutating the fixed/source road graph.

## Growth intent

S06-T11 uses two provisional growth intents only:

- `LOCAL` for pairs whose anchors belong to the same canonical `ZoneClass`;
- `COLLECTOR` for pairs whose anchors belong to different canonical `ZoneClass` values.

These values are heuristics for choosing growth candidates. They are **not** the authoritative generated-road class. Configurable arterial/collector/local classification remains S06-T12.

## Candidate set and determinism

All unordered anchor pairs are evaluated in stable `anchor_id` order. Pairs already present in the S06-T10 baseline are excluded even when their delegated least-cost route failed; S06-T11 does not silently retry a baseline edge under a different semantic role.

Remaining candidates are split by growth intent and sorted by straight-line metric distance, then by stable endpoint ids. Routing alternates `COLLECTOR`, `LOCAL`, `COLLECTOR`, `LOCAL` while both queues have candidates; when one queue is empty or its shortest remaining pair cannot fit the current direct-distance budget, the other queue may proceed.

The complete candidate-pair evaluation is explicitly bounded by `RuleBasedRoadGrowthPolicy.max_candidate_pairs`. The default is `499,500`, equal to all unordered pairs for the current S06-T08 maximum of 1,000 anchors.

## Bounded routing and length budget

Expensive pair routing is delegated to the existing S06-T09-compatible pair connector only for selected candidates. Two independent limits bound work/output:

- `max_iterations` defaults to 200 delegated pair searches;
- `max_added_length_m` defaults to 25,000 metres of accepted routed geometry.

A candidate whose straight-line metric distance already exceeds the remaining length budget is not routed. Because candidates of each intent are distance-sorted, a queue whose next candidate cannot fit can be skipped without scanning longer candidates from that queue.

A delegated `CONNECTED` route can still be longer than its direct metric distance because of hard exclusions and suitability costs. If the routed `length_m` exceeds the remaining budget, the attempt is preserved as `LENGTH_BUDGET_REJECTED` and contributes no accepted length.

## Failure semantics and diagnostics

Delegated `NO_PATH` and `SEARCH_LIMIT_REACHED` outcomes are preserved and do not abort the whole growth pass. Diagnostics report candidate evaluations, eligible and attempted pairs, accepted local/collector counts, failed/rejected outcomes, visited raster cells, accepted length, and the stable stop reason:

- `CANDIDATES_EXHAUSTED`;
- `ITERATION_LIMIT`;
- `LENGTH_BUDGET`.

The result also retains every routed attempt and its full `LeastCostConnectionResult` for provenance.

## Scope boundary

S06-T11 adds a bounded deterministic growth delta between existing anchors. It does not node or union the new geometry into `RoadGraph`, mutate fixed/source roads, assign final road classes, persist `GeneratedRoad`, or perform final network validation. Road classification is S06-T12 and final road validation is S06-T13.
