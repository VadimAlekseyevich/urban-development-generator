# Canonical Pipeline Model

> **Status: Accepted**
>
> This document owns the pipeline/stage execution contract.

## 1. Problem

The repository already contains strong algorithmic capabilities and persistence primitives, but the original legacy `core/urban_generator/pipeline/service.py` used a second untyped model based on `PipelineStage` and `dict[str, Any]`. That model is not canonical and must not guide future work.

The canonical model is built on `core.urban_generator.domain.Stage`, `StageResult`, `RunContext`, `TerritorySnapshot`, and infrastructure-neutral ports.

## 2. Layering

The execution architecture has four layers:

```text
algorithm capability
    ^
typed stage adapter (core)
    ^
execution/orchestration application service
    ^
worker/job/persistence adapters
```

Algorithm modules do not need to be rewritten to become orchestration-aware. A stage adapter composes existing algorithms behind the common Stage contract.

## 3. Core Stage contract

A core stage owns stable metadata:

- `name`;
- `version`;
- `dependencies`.

It validates an explicit typed input and executes with:

- immutable `TerritorySnapshot`;
- immutable `RunContext`;
- typed stage input;
- typed/versioned stage configuration.

It returns `StageResult[T]`.

No Stage implementation may import FastAPI, SQLAlchemy, Redis, ARQ, React, or worker-specific types.

## 4. StageResult boundary

Core `StageResult` contains only algorithm-owned output:

- typed output;
- deterministic fingerprint;
- structured non-fatal diagnostics.

It deliberately does **not** own:

- DB lifecycle state;
- progress percentage;
- artifact publication state;
- retry count;
- timestamps;
- persisted input/config hashes;
- cancellation state.

Those belong to the execution/persistence layer and are represented by `RunStageResult`, `Job`, `Artifact`, and orchestration services.

This separation supersedes older wording in `DEVELOPMENT_PLAN.md` that described all persistence/execution metadata as fields of core `StageResult`.

## 5. RunContext boundary

Core `RunContext` owns deterministic run semantics:

- run id;
- mode;
- seed / namespaced RNG factory;
- working CRS;
- immutable config references;
- correlation identifiers.

Cancellation tokens, loggers, DB sessions, repositories, queue clients, clocks, and progress reporters are **not** fields of core `RunContext`.

Execution services may carry those concerns in a separate orchestration context. This separation prevents infrastructure leakage into algorithm code.

### 5.1. PipelineContext assembly boundary

UG-AI-064 / S12-T02 defines the immutable core `PipelineContext` consumed by later orchestration.
It aggregates already-canonical contracts rather than introducing a second pipeline model:

- `RunContext` for deterministic run identity/mode/seed/CRS/config provenance;
- `TerritorySnapshot` for immutable fixed territory inputs;
- typed resolved per-stage config objects;
- infrastructure-neutral `PipelinePorts`.

Each resolved config binding uses the canonical stage-name syntax, carries a `ConfigRef` that must
already exist in `RunContext.config_refs`, and contains a typed core config object. A raw
`dict`/mapping or `None` is not a resolved config and must be decoded/validated before crossing
the core boundary. Callers retrieve config values with an explicit expected type.

`PipelinePorts` reuses existing ports rather than defining adapter-specific interfaces:
`ArtifactStore` is required, and `NetworkBackend` may be supplied when a compatible routable
snapshot already exists. Port implementations are injected runtime capabilities; SQLAlchemy
sessions/repositories, storage paths and queue clients are not pipeline-context fields.

Assembly enforces one metric working CRS across `RunContext`, `TerritorySnapshot` and any
provided `NetworkBackend`.

UG-AI-065 / S12-T02 implements that persistence boundary in
`backend.app.adapters.pipeline_context.SqlAlchemyPipelineContextAdapter`. The adapter owns the
SQLAlchemy `Session` and reads `GenerationRun`, its `Project`, and linked `DatasetVersion`
rows, but returns only the canonical core `PipelineContext`.

Persistence mapping rules are explicit:

- run id/mode/seed/working CRS become `RunContext`;
- the persisted run config gets one stable `ConfigRef`, and a `PersistedStageConfigResolver`
  decodes a defensive copy of `config_json` into typed `ResolvedConfigBinding` values;
- the project boundary becomes a `BOUNDARY` ref whose opaque identity includes a SHA-256 of
  canonical geometry WKB, so WKT/WKB ORM loading differences do not alter its identity;
- linked ready dataset versions become typed snapshot layer refs using existing
  `SnapshotLayerKind`; unknown kinds and non-ready versions are rejected;
- project, run, boundary, snapshot, and any supplied network backend must agree on working CRS;
- `ArtifactStore` and optional `NetworkBackend` implementations are injected without exposing
  their infrastructure types through core.

The adapter deliberately does not guess rich stage policy objects from generic JSON. Config
decoding stays behind the injected resolver boundary until a versioned persisted stage-config
schema exists. No ORM entity or `Session` crosses into core, and no checkpoint identity/reuse is
implemented by this adapter.

## 6. Stage adapters for existing capabilities

Before new infrastructure feature work continues, completed algorithmic capabilities S04-S09 must have canonical adapters for the coarse pipeline stages:

```text
evaluate_constraints / suitability
zoning
roads
blocks_and_parcels
buildings
demography
```

Adapters should be thin composition boundaries. They must reuse existing algorithms and existing persistence writers; they must not duplicate the algorithm itself.

A stage adapter is responsible for:

- validating its typed input;
- composing existing domain services;
- producing a typed output;
- using only `RunContext.rng(namespace)` for randomness;
- producing a deterministic fingerprint from canonical inputs/config/version;
- returning diagnostics without performing persistence.

## 7. Dependency graph

Stage dependency metadata has one canonical identity vocabulary. The v1 coarse DAG is:

```text
prepare_snapshot
 -> evaluate_constraints
 -> suitability
 -> zoning
 -> roads
 -> blocks_and_parcels
 -> buildings
 -> demography
 -> infrastructure
 -> final_validation
 -> metrics
 -> persist_manifest
```

UG-AI-062 / S12-T01 implements `StageRegistry` directly over the existing canonical `Stage`
protocol. Registry construction validates each stage's existing `name`, `version` and
`dependencies` metadata, rejects duplicate stage names, rejects dependencies that are absent from
the assembled registry, and rejects dependency cycles. Registry insertion order is retained only as
assembly/debugging information and is explicitly not an execution order.

UG-AI-063 / S12-T01 adds deterministic planning semantics to the same registry. Topological order
is dependency-safe and independent of registry insertion order; when multiple stages are ready at
the same time, stable stage name is the tie-breaker.

Explicit skip requests produce an immutable plan in that topological order:

- a requested stage is marked with skip reason `REQUESTED`;
- any non-requested stage whose direct dependency is already skipped is marked
  `DEPENDENCY_SKIPPED`;
- dependency skips propagate transitively through downstream stages;
- the plan records the directly blocking dependency names in stable order;
- an explicit request takes priority over dependency-derived reason when both apply;
- stages outside the downstream closure remain executable.

Skip is an execution exclusion decision, not checkpoint reuse. S12-T03 checkpoint reuse has its own
provenance rules and must not be represented as a skip. The core plan does not persist status,
execute stages or own worker lifecycle.

No second enum, metadata vocabulary or execution contract such as legacy `PipelineStage` may
become an independent source of stage identity.

## 8. Persistence mapping

`RunStageResult` persists execution/provenance for one stage/run. It uses the same stable `stage_name` and `stage_version` as the core Stage.

Core fingerprint and execution input/config hashes are related but distinct:

- core Stage fingerprint identifies deterministic stage output from canonical semantic parts;
- persisted `input_hash` identifies the resolved execution inputs, including dependency output identities when S12 assembles the checkpoint key;
- persisted `config_hash` identifies resolved stage configuration;
- persisted `output_fingerprint` stores the exact string value of `StageResult.fingerprint`.

`output_fingerprint` is nullable only for compatibility with pre-stabilization rows and incomplete/failed executions. New successful S12 stage executions must persist it.

UG-AI-066 / S12-T03 defines checkpoint eligibility as the exact four-part identity:

```text
(stage_name, stage_version, input_hash, config_hash)
```

The hashes remain independent. `config_hash` is computed only from canonical resolved stage
configuration parts in its own hash namespace. `input_hash` is computed from canonical current
stage input parts plus the direct dependency output fingerprints in the exact order declared by
`Stage.dependencies`.

Dependency provenance is structural rather than set-like: each dependency entry carries its stable
stage name and canonical `StageFingerprint`, and missing, extra or reordered dependency outputs
make resolved input identity invalid. Length prefixes and type markers keep string/byte parts
unambiguous.

The current stage's own `StageResult.fingerprint` is deliberately **not** a field of checkpoint
identity. It is only available after execution and remains the separate persisted
`RunStageResult.output_fingerprint`. Therefore an output fingerprint is never substituted for
the same stage's `input_hash` or `config_hash`.

UG-AI-067 / S12-T03 implements persistence-side reuse through
`backend.app.adapters.checkpoints.SqlAlchemyCheckpointStore`.

Reuse is deliberately **same-run only**. Current generated tables and stage rows are run-scoped, so
cross-run checkpoint lookup would claim reuse without copying/materializing run-owned outputs.
Exact rerun/cross-run provenance remains S12-T11 work.

For one stage resolution the store:

- loads every declared direct dependency from the same `GenerationRun`;
- requires each dependency row to be `succeeded` with non-null canonical
  `output_fingerprint`;
- returns dependency fingerprints in exact `Stage.dependencies` order;
- rebuilds the current `CheckpointIdentity` from those dependency fingerprints plus the current
  input/config parts;
- looks up only the same run/stage candidate;
- treats stage version, `input_hash` or `config_hash` mismatch as a cache miss;
- treats any non-`succeeded` candidate as a cache miss;
- requires an otherwise exact successful candidate to have a non-null canonical
  `output_fingerprint`, otherwise provenance is incomplete and reuse is rejected with an error.

A reuse hit returns typed checkpoint metadata and the persisted output fingerprint. It does not
execute a stage, mutate its row, reconstruct typed stage output, or change status.

UG-AI-068 / S12-T04 implements the first worker-owned execution path:

- `GenerationJobService` consumes an explicitly assembled `GenerationRuntime`: immutable
  `PipelineContext`, canonical validated `StageRegistry`, typed `GenerationInputResolver`,
  and optional canonical skip requests. `SqlAlchemyGenerationRuntimeFactory` composes that
  runtime through the existing persistence-to-core adapter.
- The resolver returns `StageInvocation` with one candidate stage input and canonical
  `input_parts`/`config_parts`. The executor uses the existing `Stage.validate_input()` and
  the typed resolved config binding from `PipelineContext`; completed typed dependency outputs
  are exposed through a read-only mapping. It executes exactly the registry's deterministic
  topological/skip plan, without introducing another Stage vocabulary.
- `SqlAlchemyGenerationStateStore` claims a matching queued `GenerationRun` and
  DB-authoritative `generation_run` Job with row locking. It requires real persisted
  `commit_sha`, records the attempt, and commits run/job `running` before any stage.
  Each stage `running`, `succeeded` or `skipped` transition commits separately, with exact
  checkpoint input/config hashes and the successful `StageResult.fingerprint` as separate
  `RunStageResult.output_fingerprint`. Skips retain explicit reason diagnostics and never
  invent output fingerprints.
- Each executable stage resolves its input identity using **persisted**, successful direct
  dependency fingerprints in `Stage.dependencies` order. A metadata-only reusable checkpoint
  cannot stand in for an in-memory typed stage output; until typed output hydration exists, such a
  candidate is rejected rather than silently reused.
- A run succeeds only when the expected stage rows are all completed or explicitly skipped.
  Stage/attempt errors record failure in the authoritative stage/run/job rows, and ARQ never
  reports the former fake `accepted` response. A duplicate delivery of an already succeeded
  or currently running run does not start another attempt.

The runtime factory must be explicitly registered in worker context; a missing provider is a
configuration error, not a successful no-op. Rich stage policies cannot be fabricated from
persisted generic `config_json`.

UG-AI-069 / S12-T04 adds `tests/integration/test_generation_worker_e2e.py` as the PostgreSQL/
PostGIS-backed acceptance path through `worker.tasks.run_generation` (using its actual runtime
factory and state store, not a mocked persistence layer). Small canonical synthetic stages make
committed `running` stage progress visible in another DB session during execution, verify the
independent checkpoint input/config hashes and ordered persisted dependency fingerprints, and
assert successful final run/job/stage provenance. The fixture also covers deterministic requested
and dependency-propagated skips, a failure after earlier stages have committed success, a failure
of persistence-to-core assembly, idempotent redelivery of a successful run, and PostgreSQL guards
against mutating the successful run, its stage results or linked dataset-version refs. Synthetic
stages deliberately test the orchestration boundary rather than claiming a complete real-territory
GIS execution; that acceptance remains a later milestone gate.

Cooperative cancellation, retry/backoff, outbox recovery and artifact publication/GC remain their
respective later ordered tasks.

## 9. Cancellation and retry

Core algorithms remain deterministic pure/bounded computations as far as practical.

Cooperative cancellation is checked by execution orchestration between bounded units. If a long algorithm later needs internal cancellation points, introduce an infrastructure-neutral cancellation port by ADR rather than importing worker state.

Retry is an application/worker concern. Completed immutable run/source data are never mutated to simulate retry.

## 10. Network ownership

Infrastructure stages must depend on `NetworkBackend`, not NetworkX or `SpatialSnapIndex` directly.

S10-T06 therefore means adapting demand/facility/site points to the existing `NetworkBackend.snap()` contract with bounded batch semantics. It does **not** create another nearest-neighbor index.

## 11. Metrics ownership

S11 extends the already-existing `RawMetricId` / `MetricDefinition` vocabulary from `domain.benchmarking`. It must not introduce an unrelated metric ID registry.

The S11 registry adds runtime metadata needed for evaluation (scope, direction, source/version, normalization policy) around canonical raw metric IDs.

## 12. Validation ownership

The existing Constraint/ConstraintEngine/ValidationReport contract remains authoritative.

S11-T01 may extend violation detail with entity references/problem geometry, but must preserve the shared engine and hard/soft semantics. Stage-specific ad-hoc validation formats are prohibited.

## 13. Stabilization exit criteria

Feature work after S10-T05 remains blocked until:

- legacy untyped pipeline code is removed;
- S04-S09 stage adapters exist;
- an in-memory typed dependency execution fixture proves adapters compose;
- stage names/versions match persisted RunStageResult identity;
- documentation and roadmap no longer describe conflicting StageResult/RunContext ownership;
- CI is green.
