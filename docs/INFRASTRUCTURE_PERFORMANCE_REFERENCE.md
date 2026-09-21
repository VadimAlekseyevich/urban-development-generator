# Infrastructure reference performance fixture

> **Status: Complete through UG-AI-045 / S10-T14**

S10-T14 uses `benchmarks/infrastructure_reference.py` as the deterministic reference workload for
candidate×demand accessibility and greedy placement cache behavior.

## Workload

The default fixture contains:

- 64 demand subjects;
- 16 candidate sites;
- demand routing batches of 16;
- a fully reachable 1,024-row candidate×demand matrix;
- eight greedy placement iterations;
- one metric line network using EPSG:3857.

The fixture is intentionally synthetic and bounded. It is large enough to expose accidental
candidate×demand recomputation while remaining suitable for required CI.

## Routing-call counter

T07 candidate accessibility groups demand nodes into bounded batches and runs one
`NetworkBackend.multi_source_distances()` call per candidate node and demand batch.

The reference fixture therefore expects exactly:

`candidate_count × ceil(demand_count / demand_batch_size)`

routing calls. For the default workload that is `16 × 4 = 64` calls.

The benchmark wraps the real `NetworkXBackend` only to count those calls. It does not replace
routing results or use a fake distance matrix.

## Greedy cache invariant

After T07 returns the complete accessibility batch, T08 initializes
`InfrastructureGreedyPlacementState.coverage_cache`. The benchmark records the routing-call count,
runs the configured greedy iterations through:

- `calculate_infrastructure_candidate_benefits()`;
- `select_infrastructure_greedy_candidate()`;
- `apply_infrastructure_greedy_selection()`;

and then asserts that the routing-call count is unchanged.

This is the S10-T14 regression proof that greedy iterations consume the persisted in-memory T07
coverage cache instead of repeating the full candidate×demand routing workload.

## Diagnostics emitted

The benchmark result records:

- demand/candidate/subject counts;
- reachable result count;
- configured batch size;
- expected and observed routing-call counts;
- routing-call count after greedy placement;
- greedy iteration and accepted-facility counts;
- final remaining demand;
- raw accessibility and greedy elapsed milliseconds;
- a deterministic semantic digest.

Elapsed timings remain observations rather than fixed wall-clock pass/fail thresholds because
shared CI runners vary substantially. The accepted S10-T14 benchmark envelope is structural:

- fixture identity: `infrastructure-candidate-demand-v1`;
- 64 demand subjects;
- 16 candidate sites;
- 1,024 bounded candidate×demand subjects;
- demand batch size 16;
- exactly 64 T07 `multi_source_distances()` calls;
- eight greedy iterations / eight accepted generated facilities;
- routing-call count after greedy must remain exactly 64;
- deterministic semantic digest must be stable for an unchanged implementation;
- full required Python/frontend/compose CI must remain green.

## Required CI gate

UG-AI-045 adds `Infrastructure reference benchmark` to the required Python CI job after the
existing road/block/building reference workloads. The benchmark emits JSON into the CI log. The
final S10/M1 closure commit records the observed default-run diagnostics from the first green
closure run, but does not convert those machine-specific milliseconds into a hard threshold.

## Recorded closure diagnostics

The first required CI run with the dedicated infrastructure benchmark step was **CI run
35560322208** on branch head `ec01af3df7aa741f531a13f69bee2677f2e93523`, using the hosted
Ubuntu 24.04 runner image. Python, frontend and compose-smoke jobs were all green.

The default infrastructure benchmark emitted:

- `accepted_facility_count = 8`;
- `accessibility_ms = 12.87811`;
- `greedy_ms = 3.417852`;
- `reachable_count = 1024`;
- `remaining_demand = 32.0`;
- `routing_call_count = 64`;
- `routing_call_count_after_greedy = 64`;
- deterministic digest
  `8fd8a263b208e8f31820f010f020e9445948aceda87e24891508e96d65706ac7`.

The milliseconds are recorded observations, not a portable performance threshold. The enforced
regression envelope is the workload shape, routing-call/cache invariants, deterministic digest,
successful benchmark execution, and green required CI.

With that evidence, the S10-T14 performance gate is satisfied. Any later change to the reference
workload or structural envelope must update this document explicitly rather than silently weakening
the gate.
