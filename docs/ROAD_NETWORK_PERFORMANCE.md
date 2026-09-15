# Road-network reference benchmark (S06-T17)

S06-T17 adds a deterministic synthetic road-network workload for comparing the hot paths introduced in Sprint S06. It is a measurement fixture, not a machine-dependent pass/fail latency gate.

## Reference workload

The default `synthetic-grid-v1` fixture uses:

- EPSG:3857 metric working CRS;
- a 32 x 32 node grid (1,024 nodes, 1,984 routable edges);
- 64 full row/column `SemanticRoad` inputs, which node into the same 1,984 edge topology;
- 128 deterministic snapping queries against a reusable STRtree index;
- 32 deterministic end-to-end path queries, measured with both Dijkstra and A*.

The runner validates structural checksums before reporting timings. A semantic change that alters reference junction/part counts, loses snap matches, disconnects the grid, or makes Dijkstra and A* disagree fails the benchmark run.

## Run locally

```bash
uv run python -m benchmarks.road_network_reference
```

The command emits JSON containing fixture sizes and milliseconds for:

- snapping index construction;
- snapping query batch;
- semantic noding;
- Dijkstra pathfinding batch;
- A* pathfinding batch.

For exploratory scaling, `--grid-size`, `--spacing-m`, `--snap-queries`, and `--path-queries` can override the defaults. Comparisons intended as a regression baseline should use the unchanged defaults and the same machine/runtime environment.

## CI policy

CI executes the default benchmark after the test suite and records the JSON in the job log. Timing values are intentionally not compared to fixed millisecond thresholds because shared CI runners vary substantially. Correctness and workload shape are enforced by unit tests; timing trends can be compared between commits or captured by a dedicated stable runner later if the project adopts a performance budget.
