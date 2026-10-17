# S14-T07 — 25 km² and 100 km² composite territory profiles

**Task:** UG-AI-101. The profiling contract is
`benchmarks/territory_composite_profile.py`.

## Reproduce on the same machine

Run **each area in a fresh Python process** so the cumulative process
high-water RSS belongs to that profile, rather than the previous size.

```sh
uv run python -m benchmarks.territory_composite_profile \
  --area-km2 25 --output /tmp/udg-territory-25km2.json
uv run python -m benchmarks.territory_composite_profile \
  --area-km2 100 --output /tmp/udg-territory-100km2.json
```

The Python CI job executes both against the same pinned dependencies, then
uploads their complete JSON as a 14-day GitHub Actions artifact
`territory-composite-25-100`. Running the command locally requires no DB,
cloud connection or account; each real windowed raster uses temporary local
files and a real `LocalArtifactStore` that cleans up afterward.

## Spatial scale and real implementation coverage

The **nominal projected** territories are squares at EPSG:3857 coordinates
near the equator, with exact 5,000 m and 10,000 m sides. This is a fixture
area in *projected metres*, not measured geodesic land area. Spatial extent
and load parameters are derived deterministically from the same side length.

| Workload | 25 km² | 100 km² | Authoritative code executed |
| --- | ---: | ---: | --- |
| Territory side | 5,000 m | 10,000 m | `TerritoryWorkload` |
| Road grid nodes | 21² = 441 | 41² = 1,681 | `SemanticNoder`, STRtree snapping, `NetworkXBackend` Dijkstra/A* |
| Noding candidate pair cap | 441 | 1,681 | `SemanticNodingDiagnostics.candidate_pair_count` |
| Block / zone fixtures | 12² = 144 | 24² = 576 | `BlockZoneAssociator`, `SimplifiedParcelSubdivider` |
| Building proposals | 1,000 | 3,000 | `BuildingPlacementConverger`, attribute and area metrics |
| Infrastructure demands × sites | 64 × 12 = 768 | 128 × 24 = 3,072 | `compute_candidate_site_accessibility_batch`, greedy coverage-cache placement |
| Expected bounded network calls | 48 | 192 | Candidate × ceil(demand / batch 16) |
| Raster resolution | 10 m | 10 m | Existing `RasterNormalizer` WarpedVRT + tiled GeoTIFF |
| Raster sample cells | 500² = 250,000 | 1,000² = 1,000,000 | 1-band source and normalized result |
| Raster tile windows, 256² max | 4 | 16 | `NormalizedRasterArtifact.windows_written` |

The raster image fills the territory; the road reference fills the side;
building grid spacing is tied to side length, and block / infrastructure
locations are **synthetic simplified component fixtures**, not real OSM
feature distributions. The five real reference components are run under one
area/workload policy but **do not hand their outputs to each other**.
Consequently, the benchmark is a **composite integration/performance profile**,
not an end-to-end test of the canonical generation Stage DAG, actual real-world
land suitability, persistence, or full scenario generation. The production
runtime composition blocker must be assessed independently.

## Work budgets, metrics and decision rules

The versioned output schema is `s14-territory-composite-v1`. Each report
contains exact input/workload counts, operation limits, observed counters,
per-phase and total wall milliseconds, cumulative process high-water RSS MiB,
fixture identity, CRS, Python/NetworkX/Shapely/Rasterio versions, platform,
architecture, CPU model/count, physical host memory, CI flag and optional
`GITHUB_SHA`. Hardware metadata deliberately contains no hostnames or
personally identifying paths.

**Hard CI contract:** fail if any canonical operation counter exceeds its
declared bounded budget, or if fixture identity changes: expected road
candidate-pair count, accepted building proposals, number of parcels,
raster coverage/window count, full infrastructure candidate×demand coverage
and exact routing call count. Greedy selection must not issue additional
routing calls after the precomputed T07 coverage cache. No new cache or
algorithm is created by this task: it measures the canonical ones.

**Advisory machine-dependent budgets:** 240,000 ms wall duration and
1,536 MiB whole-process high-water RSS per profile. Their pass/fail flags
are archived as observations, **not enforced as hard CI gates**. CI runner
hardware, process startup, resource contention, platform-specific `ru_maxrss`
units and native-library caches all affect these numbers. The host's reported
physical memory may not match a cgroup container memory limit. Do not infer
single-stage peak memory from cumulative process RSS.

**No performance improvement claim without before/after evidence.** Actual
Stage-DAG 25/100 km² load tests, real road shapes, raster multi-band cases,
worker scheduling/queue latency, PostGIS query plans, artifact storage
latency and release-grade memory/time SLOs still require separate evidence.
The benchmark's explicit hard bounds protect operation counts and semantic
determinism; they are not a substitute for deployment resource limits or
user-facing response time guarantees.

## Test and release gates

- `tests/unit/test_territory_composite_profile.py` validates both
  territorial scale contracts, fixed operation budgets, rejected
  unsupported scales, cache reuse, early counter failures and machine schema.
- Python CI runs **both full real reference workloads** and publishes JSON
  for diagnostic examination. An accepted reference profile must be linked to
  the successful exact-head CI run.
- All preexisting Python, frontend, Compose, and browser E2E checks remain
  mandatory; ordinary individual reference benchmarks are still preserved.
- For a proposed optimization, compare independent runs from the same
  environment with unchanged feature counts, candidate counters, checksums,
  and working CRS before altering indexes, caches, Stage inputs, or domain
  rules. The first measured baseline is the merged PR's recorded CI artifact.
