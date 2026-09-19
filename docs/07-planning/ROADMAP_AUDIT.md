# Future Roadmap Architecture Audit

> **Status: Accepted for planning**
>
> Scope: remaining S10 work through v1.0.0.

## 1. Result

The high-level S10-S15 ordering remains valid. The required change is to make several tasks extensions of already-existing contracts rather than invitations to create replacements.

No new feature sprint is inserted. A blocking STAB gate sits between S10-T05 and S10-T06.

## 2. S10 corrections

- **T06:** reuse `NetworkBackend.snap()`; do not create an infrastructure STRtree.
- **T07:** own accessibility batching/cutoffs and result matrix/service contracts. It may use `multi_source_distances()`; graph construction is not its responsibility.
- **T08:** consume T07 accessibility results and maintain incremental remaining-demand/coverage state. No pathfinding inside placement.
- **T09:** feasibility is a validation/filter boundary over candidates and proposed capacity; no second site generator.
- **T10:** persistence follows existing generated-entity run ownership and successful-run immutability.
- **T11:** metric IDs must match canonical `RawMetricId`.
- **T12:** UI is a read model over persisted/authoritative results, not placement logic.
- **T13/T14:** integration/performance gates must exercise the exact T06-T11 path.

## 3. S11 corrections

- **T01:** extend canonical `ConstraintResult/ValidationReport` with optional entity/problem-geometry detail needed by the violations layer.
- **T02/T03:** aggregate/soft rules register through the existing engine.
- **T04:** create runtime metric metadata around existing `RawMetricId/MetricDefinition`; do not create new independent IDs.
- **T05-T09:** adapters produce canonical raw metrics from existing artifacts/results; do not recompute expensive GIS where authoritative results already exist.
- **T10-T12:** normalization/score/sensitivity are downstream transformations over persisted raw metrics.
- **T13/T14:** API/UI consume persisted validation/metric models.
- **T15:** regression fixtures assert ranges/invariants and canonical metric IDs.

## 4. S12 corrections and required order

Required order:

```text
T01 DAG metadata
 -> T02 persisted PipelineContext adapter
 -> T03 checkpoint fingerprint contract
 -> T04 real generation worker
 -> T05 cancellation
 -> T06 retry
 -> T07 outbox hardening
 -> T08 artifact publish/GC
 -> T09/T10 batches
 -> T11 exact rerun
 -> T12 provenance
 -> T13 compare
 -> T14/T15 UI
```

T01 must orchestrate the Stage identities stabilized in M0. T02 maps persistence into core types/ports but does not let ORM types enter core. T03 uses stage version + input/config/dependency provenance. T04 is the first point where `worker.run_generation` becomes real.

## 5. S13 corrections

- layer catalog precedes all generalized delivery/UI work;
- bbox delivery is not removed by MVT; it remains a bounded fallback/query path;
- MVT uses immutable run/dataset scope and existing spatial indexes;
- frontend layer registry is required before full layer tree/workspace;
- exports are jobs and publish Artifacts;
- S3/MinIO implements ArtifactStore parity only;
- Playwright proves the complete browser workflow rather than individual panel rendering.

## 6. S14 corrections

Existing CI already runs migration smoke. T13 therefore means release-grade migration-from-zero coverage including all current migrations/integration checks, not a duplicate command.

Profiling tasks produce measurements before changes. Any architecture-changing optimization requires ADR review.

Reliability E2E must cover:
- duplicate delivery;
- worker crash during a stage;
- transient retry;
- cancellation;
- stale checkpoint;
- orphan temp artifact;
- successful-run immutability.

## 7. S15 corrections

Experiment runner creates runs through the same scenario/orchestration contracts. It is not a parallel research execution framework.

Research uses canonical RawMetricId, provenance manifests, dataset versions, configs and seeds. Offline demo data is an input package; fallback screenshots/video are not accepted as proof that the live pipeline works.

## 8. Release corrections

R1-R9 are acceptance gates over one release candidate commit. R10 is a consequence, never a task that can be completed independently.

## 9. Cross-sprint architecture invariants

Every future item must preserve:

- one Stage model;
- one Constraint family;
- one NetworkBackend port;
- one ArtifactStore port;
- one Job/outbox authority;
- one fixed/generated ownership model;
- one raw metric vocabulary;
- one run provenance lineage;
- same pipeline for EXPANSION and FROM_SCRATCH.
