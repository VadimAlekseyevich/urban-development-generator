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

S12 checkpoint reuse requires stage name/version plus independently resolved input/config provenance to match. Dependency output fingerprints participate in resolved input identity; an output fingerprint is never substituted for the input/config hashes of the same stage.

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
