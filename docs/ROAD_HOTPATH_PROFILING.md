# S14-T05 — Road noding / snapping / routing hotspot evidence

**Task:** UG-AI-099. This profile measures the **real** S06
`SemanticNoder.node`, `SpatialSnapIndex.candidates` and
`NetworkXBackend.shortest_path` adapters on a reproducible metric grid.
It adds *only benchmark-side* route instrumentation, no production mutation
or duplicate graph/CRS/Stage vocabulary.

## Reproduce

```sh
uv run python -m benchmarks.road_network_reference
uv run python -m benchmarks.road_hotpath_profile \
  --grid-sizes 8 16 32 --repetitions 2 \
  --snap-queries 128 --path-queries 32 \
  --output /tmp/udg-road-hotpath-profile.json
```

The CI Python job runs the same workload and publishes 14-day artifact
`road-hotpath-profile`. Each grid has `size²` nodes,
`2 × size × (size - 1)` undirected edges, and `2 × size`
two-dimensional semantic roads (rows and columns); edge weights are in
metres (EPSG:3857). Snap tolerance is kept strictly below half a cell,
so each query has exactly one matching target. Path sources and targets
are deterministic. This is a synthetic regression fixture, **not**
a representative urban-road distribution or production SLO.

## What is actually measured

- **Snapping:** STRtree index construction milliseconds; all queries'
  milliseconds; `snap_candidate_count`, maximum candidates per query,
  snap success count and index target count. The benchmark uses
  `candidates()`, whose first ordered result is exactly `snap()`'s
  answer. It does **not** allocate an all-points-by-targets matrix.
- **Noding:** `SemanticNoder.node` elapsed milliseconds and the
  existing canonical diagnostics:
  `road_part_count`, `candidate_pair_count`,
  `suppressed_intersection_count`, `overlap_pair_count`,
  `junction_count`, `output_part_count`. The square fixture must have
  `candidate_pair_count == size²`, not the quadratic pair count over
  arbitrary road parts.
- **Routing:** Dijkstra and A* wall-clock milliseconds and distance
  checksums, plus actual `_edge_weight()` calls (edge relaxation/evaluation
  count) and `_heuristic()` calls per algorithm. The two wrappers are
  in **`benchmarks/road_network_reference.py` only**, never on a shared
  production adapter; counters reset between algorithms. Dijkstra and A*
  must agree on total metres. Edge evaluations are bounded above by
  `2 × graph edge count × number of path queries`. These are not
  “visited node counts” and are not labelled as such.
- **Repetition and comparison:** each size is profiled twice and the JSON
  archives full individual runs and per-stage min, median and max elapsed
  time, CPU architecture and Python/NetworkX/Shapely versions. Work counts
  and route checksums must match *exactly* between repetitions; elapsed
  milliseconds may vary with runner load.

## Interpretation and next optimization

The existing spatial candidate indexing is already present; **no
new index or routing algorithm is justified solely by this profile**.
Compare measured counts first, followed by timings and graph topology.
In the CI evidence:
- Pathfinding may be dominated by Python queue/graph traversal at larger
  sizes even when the number of examined edges stays linear per query.
- Semantically split road segments add geometric intersection cost; benchmark
  noding count is a lower-complexity regular grid, not a guarantee for dense
  overlapping or multilayer street networks.
- Distances and semantic suppression behavior remain tested independently
  by existing S06 unit/integration tests.
- Do not convert one noisy CI median into a performance pass/fail gate or
  claim real-world 25/100 km² throughput. Representative, budgeted
  integrated-scale measurements remain S14-T07 (UG-AI-101).

**Regression gates:** `tests/unit/test_road_hotpath_profile.py` checks
actual candidate counts, deterministic per-algorithm work counters, bounded
profiling configuration, multi-scale metric names, and CLI JSON emission.
The ordinary reference graph benchmark and existing road/network tests also
remain mandatory CI gates.


## First measured CI baseline — 2026-10-14

Exact feature-head `eff64858b2b1fbb869e46d159687d2bc409ba92c`,
[CI run 38540611757](https://github.com/VadimAlekseyevich/urban-development-generator/actions/runs/38540611757):
all four jobs succeeded. [Download measured JSON](https://github.com/VadimAlekseyevich/urban-development-generator/actions/runs/38540611757/artifacts/11881848443)
from the actual retained CI artifact (`road-hotpath-profile`, 14 days).

The table reports the **median of two runs**, milliseconds, on GitHub's
CI runner (Python 3.12.3). It is not a production CPU benchmark or a
threshold for deployment.

| Grid | Nodes | Snap (ms, 128 queries) | Noding (ms) | Dijkstra (ms, 32 paths) | A* (ms, 32 paths) | Noding pairs | Evaluated edges Dijkstra / A* |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8×8 | 64 | 2.597 | 19.860 | 5.548 | 5.046 | 64 | 3,428 / 2,624 |
| 16×16 | 256 | 2.561 | 96.823 | 22.762 | 19.835 | 256 | 14,684 / 10,240 |
| 32×32 | 1,024 | 2.532 | 500.516 | 94.461 | 76.533 | 1,024 | 60,025 / 40,256 |

Each case returned exactly **128 snap candidates** (one per query) and
Dijkstra/A* had identical canonical distance checksums. Noding grows
faster in elapsed time than the noding-candidate counter itself on this
small reference workload; this alone does not identify a safe code
optimization without profiles and denser topology. Avoid speculative
STRtree or routing rewrites from one small synthetic runner.
