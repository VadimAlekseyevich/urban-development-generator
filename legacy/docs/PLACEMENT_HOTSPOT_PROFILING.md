# S14-T06 — Building and infrastructure hotpath profiling

**Backlog:** UG-AI-100. Production/domain contracts are unchanged; the
reference experiments use deterministic, deliberately synthetic fixtures.
The task profiles the **actual** canonical building converger and
infrastructure T07/T08 routing/greedy placement rather than inventing a
parallel algorithm or an isolated mock workload.

## Reproduce

```sh
uv run python -m benchmarks.placement_hotpath_profile \
  --building-counts 1000 3000 \
  --infrastructure-scales 32x8 64x16 \
  --repetitions 2 \
  --output /tmp/udg-placement-hotspot-profile.json
```

The numbers are building proposals, then demand blocks × candidate facilities.
The profiler returns JSON including Python/platform, process high-water RSS,
all existing reference timings/outputs, new fine-grained timings,
`cProfile` top 12 domain functions (cumulative/self time and call counts),
and unprofiled repeat-wall times. The first run is instrumented and **must
not** be compared directly to unprofiled wall time. Cumulative times overlap
across nested functions; they are not additive. Reported RSS is process
high-water over *all* workloads, not per-stage incremental allocation.

The script checks the deterministic output digest is identical across
repetitions. Bounds: ≤10,000 building proposals, ≤256 infrastructure demands,
≤64 candidates, ≤4 scales per family, and 1–4 repetitions. These protect the
reference CI runner; production domains retain their own canonical limits.

## Instrumented operations and invariants

**Building placement:** proposal-construction, convergence, attribute
assignment, authoritative area metrics. Diagnostic counts from
`BuildingPlacementConvergenceDiagnostics` expose proposal/iteration counts,
spacing rejections, target-window rejections and accepted placements.
The accepted count, coverage/FAR and deterministic digest remain exact, so a
faster implementation cannot simply skip collision/target checks.

**Infrastructure:** T07 `compute_candidate_site_accessibility_batch` populates
one routing coverage cache; T08 calculates benefits, selects a candidate,
then applies the cached coverage. These three T08 phases have individual
measured timings. Total candidate evaluations, cache entries and actual
reachability rows are recorded. The same instrumented
`_CountingNetworkXBackend` verifies `routing_call_count_after_greedy`
is identical to the original T07 call count, including across multiple greedy
iterations: **no candidate-demand routing is repeated while selecting
facilities**. The fixture demands every pair reachable so its full
candidate×demand cache row count is also verified. Production sparse-network
cases still require separate scenario measurements.

## Acceptance and caveats

- CI runs 1k/3k buildings and 32×8/64×16 infrastructure and retains JSON
  artifact `placement-hotspot-profile` for 14 days.
- Dedicated unit tests verify diagnostics and that cached greedy placement
  does not reroute. Existing 1k/10k building and infrastructure reference
  benchmark gates continue to run.
- **No cache rewrite or index migration without measured improvement.** This
  is CPU/in-memory geometry and routing work, not a SQL query plan. SQL index
  changes require S14-T03's independent `EXPLAIN ANALYZE` evidence.
- Timings are diagnostic measurements on CI hardware, **not throughput SLOs**.
  Variance, fixture representativeness and real 25/100 km² end-to-end
  performance require S14-T07. The production runtime blocker #178 remains
  independently tracked.

