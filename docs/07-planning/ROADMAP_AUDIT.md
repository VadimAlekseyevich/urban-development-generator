# Future Roadmap Architecture Audit

> **Status: Accepted after M0 stabilized-contract final pass**
>
> Scope: remaining S10 work through v1.0.0.

## 1. Result

The high-level S10-S15 ordering remains valid. The required change is to make several tasks extensions of already-existing contracts rather than invitations to create replacements.

No new feature sprint is inserted. The finite M0 gate sits between S10-T05 and S10-T06; after M0 closes, the next feature task is UG-AI-015 / S10-T06.

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

T01 must orchestrate the Stage identities stabilized in M0. T02 maps persistence into core types/ports but does not let ORM types enter core. T03 uses stage name/version + independent input/config hashes, with ordered dependency `RunStageResult.output_fingerprint` values participating in resolved input identity; successful outputs persist their own fingerprint separately. T04 is the first point where `worker.run_generation` becomes real.

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


## 10. Final stabilized contract matrix

Future implementation extends the following M0 contracts rather than replacing them.

| Future scope | Canonical contracts that must be extended |
| --- | --- |
| S10 infrastructure | `DemographyStageOutput.demand_profile`, `RoadStageOutput.graph`, `NetworkBackend`, generated/run ownership, `RawMetricId` |
| S11 validation/metrics | `ConstraintEngine` / `ConstraintResult` / `ValidationReport`, authoritative Stage metric outputs, `RawMetricId` / `MetricDefinition` |
| S12 orchestration | `Stage` / `StageResult`, `RunContext`, `TerritorySnapshot`, `RunStageResult` including `output_fingerprint`, `Job`, `JobOutbox`, `ArtifactStore` / `Artifact` |
| S13 delivery/export/UI | persisted run-scoped entities/read models, future canonical `LayerCatalog`, existing `ArtifactStore`, shared frontend layer registry |
| S14 hardening | the same S12/S13 runtime, Job/outbox/artifact state machines, existing benchmark/CI paths; no parallel reliability framework |
| S15 experiments | normal `ScenarioBatch`/S12 runs, immutable provenance, canonical raw metrics and validation outputs |
| R1-R10 | the same production pipeline and contracts used by normal runs; no release-only execution path |

### Contract-extension rule

A UG-AI task is not ready if it cannot name the row above that owns its cross-cutting boundary.
If implementation would require a second Stage, Constraint, NetworkBackend, ArtifactStore, Job
authority, generated-state model, raw-metric vocabulary, or run lineage, stop and create an ADR
rather than implementing the duplicate.

## 11. Final ordering conclusion

The audited critical path remains:

```text
S10 infrastructure
 -> S11 final validation/raw metrics/score
 -> S12 durable orchestration/scenarios
 -> S13 scalable delivery/export/workspace
 -> S14 reliability/performance
 -> S15 experiments/demo
 -> R1-R10 acceptance
```

No remaining task requires moving a later sprint ahead of an earlier one. UI vertical slices may
follow their stabilized backend contracts, but no milestone or release gate may be claimed from
isolated modules.
