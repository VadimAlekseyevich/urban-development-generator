# Architecture Debt Audit

> **Status: Active — blocking feature work**
>
> Audited baseline: `main@405890d820751d07156099f6f7073f22cdd083fa`
>
> Audit date: 2026-09-19

## 1. Executive result

The project has substantial, well-tested domain capabilities and a strong persistence foundation, but it is **not architecture-ready for S10-T06 yet**.

The main debt is integration debt, not algorithm quality: early contracts exist, but several completed algorithmic sprints are not yet connected through the contracts that were intended to govern the full pipeline.

Feature work is blocked until all Critical/High stabilization findings below are resolved or explicitly superseded by ADR.

## 2. Confirmed strengths

The audit confirmed:

- `core` is infrastructure-independent in the reviewed module graph;
- request endpoints use application services rather than embedding SQL query details;
- repository ports isolate DB access for project/layer read paths;
- working CRS is explicitly metric and EPSG:4326 is rejected for `RunContext`;
- deterministic namespaced RNG exists;
- `NetworkBackend` hides NetworkX;
- reusable STRtree snapping already exists and is used by `NetworkXBackend`;
- fixed/generated ownership and run scoping are represented in persistence;
- `RunStageResult`, `Artifact`, `Job`, and outbox foundations already exist;
- successful runs and ready dataset versions have immutability guards;
- CI currently checks lint, mypy, tests, benchmarks, migration smoke, frontend build, and Docker Compose smoke;
- baseline HEAD CI is green.

## 3. Blocking findings

### AD-001 — Legacy second pipeline model
**Severity:** Critical  
**State:** Open

`core/urban_generator/pipeline/service.py` and `stages.py` define an untyped `dict[str, Any]` pipeline and separate `PipelineStage` identity while `domain.Stage` already exists.

No reviewed production/test code depends on the legacy model outside those files.

**Required resolution:** remove the legacy model and make `domain.Stage` the only canonical pipeline contract.

### AD-002 — Stage contract is not adopted by completed S04-S09 capabilities
**Severity:** Critical  
**State:** Open

The Stage protocol is tested in isolation, but repository-wide review found no S04-S09 algorithmic stage implementing it.

Suitability, zoning, roads, blocks/parcels, buildings, and demography are currently strong component libraries/vertical slices rather than one typed pipeline.

**Required resolution:** add thin stage adapters and an integration spine fixture before continuing infrastructure.

### AD-003 — Generation worker is a placeholder
**Severity:** High  
**State:** Open / intentionally deferred functionality

`worker.tasks.run_generation()` validates the UUID and returns `{"status": "accepted"}`; it does not execute a run.

This is acceptable only while S12 is future scope, but it must be clearly documented as unavailable rather than interpreted as completed orchestration.

**Required resolution now:** make readiness/docs explicit.  
**Required implementation later:** S12-T02–T07, after typed adapters are stable.

### AD-004 — Development plan overstates core RunContext/StageResult responsibilities
**Severity:** High  
**State:** Open

The plan says `RunContext` contains cancellation/logger and `StageResult` contains artifacts/metrics/counters/warnings. The implemented core contracts intentionally do not.

Mixing those concerns into core would leak execution infrastructure.

**Required resolution:** canonicalize the narrower core boundary in PIPELINE_MODEL and update DEVELOPMENT_PLAN wording.

### AD-005 — Future S10-T06 wording would duplicate existing snapping infrastructure
**Severity:** High  
**State:** Open

Road work already implemented `SpatialSnapIndex` and `NetworkBackend.snap()`. The roadmap currently says “Reusable nearest index on graph snapshot”, which can encourage a second infrastructure index.

**Required resolution:** redefine T06 as a bounded infrastructure-domain batch adapter over `NetworkBackend.snap()`.

### AD-006 — Future S11-T04 would duplicate existing metric vocabulary
**Severity:** High  
**State:** Open

`domain.benchmarking` already defines `RawMetricId`, `MetricDefinition`, canonical raw metrics, experiments, diagnostics, and the v1 reference profile.

**Required resolution:** S11 metric registry must extend this vocabulary with runtime metadata; it must not replace or fork metric IDs.

### AD-007 — ValidationReport is intentionally too small for future violations layer
**Severity:** Medium / planned extension

Current `ConstraintResult` contains code/severity/scope/pass/message but no entity reference or problem geometry. S11-T01 explicitly needs those.

**Required resolution:** extend the canonical constraint result/report contract in S11-T01 before API/UI violation delivery. No separate violation DTO should become the domain authority.

### AD-008 — Work-item completion count can overstate integrated readiness
**Severity:** Medium  
**State:** Open

The README reports 127/210 items complete, but work-item arithmetic does not show whether capabilities compose end-to-end.

**Required resolution:** keep count as informational only and report architecture readiness + milestone state separately.

### AD-009 — Frontend composition is accumulating in App-level wiring
**Severity:** Medium / future

The current UI correctly delivers stage vertical slices, but the final workspace would become difficult to evolve if each future layer continues to be manually wired into the root component.

**Required resolution:** S13-T05 frontend layer registry is an architecture gate, not cosmetic refactoring. S10–S12 UI additions may add panels but must not invent a competing layer-state model.

## 4. Future-roadmap architecture audit

### S10 — Infrastructure/accessibility
Keep the sprint, but rewrite T06 around existing `NetworkBackend`. T07 owns batched accessibility; T08 may cache coverage incrementally but must consume T07 outputs. T09 is feasibility validation, not a second placement engine. Persistence starts only after domain contracts are stable.

### S11 — Validation/metrics/score
Extend existing Constraint and benchmarking contracts. Preserve raw metrics independently from normalization/score. Violation geometry belongs to the canonical validation result family. Score sensitivity operates on persisted raw metrics without rerunning GIS.

### S12 — Orchestration/scenarios
This sprint is where executable DAG, persisted context assembly, checkpoints, real worker execution, cancellation, retries, outbox hardening, manifests, batches, and compare belong. Stabilization must not prematurely implement those features, but must ensure S12 has one Stage vocabulary to orchestrate.

Ordering requirement:
`DAG -> persisted context adapter -> checkpoint contract -> generation job -> cancellation/retry -> outbox/artifact hardening -> batches/rerun/provenance/compare -> UI`.

### S13 — Delivery/export/UI
Layer catalog must precede MVT/full tree/workspace. Export is asynchronous and artifact-backed. S3/MinIO remains an ArtifactStore adapter and may not change core. Frontend registry must replace ad-hoc root wiring before “complete workspace” is accepted.

### S14 — Hardening
Profiling tasks produce evidence before optimization. Existing CI already performs migration-from-zero style smoke, so S14-T13 must harden the release matrix rather than duplicate the current check. Reliability E2E must exercise the same authoritative Job/outbox/artifact state machines used in production-like execution.

### S15 — Research/demo
Experiments consume canonical raw metric IDs and provenance manifests. Experiment schema may not create another configuration/run model. Offline demo artifacts are a release fallback, not an alternate execution path.

## 5. No-go rules until stabilization passes

Do not:

- start S10-T06 implementation;
- add another pipeline/stage enum;
- build infrastructure-specific STRtree snapping;
- add new metric IDs outside the canonical raw metric vocabulary without updating its owner;
- implement generation orchestration around legacy `dict[str, Any]`;
- mark architecture readiness Accepted while AD-001–AD-004 remain open.

## 6. Exit condition

This audit becomes non-blocking only when every Critical/High item is Closed or has an Accepted ADR that intentionally supersedes it, and required CI is green.
