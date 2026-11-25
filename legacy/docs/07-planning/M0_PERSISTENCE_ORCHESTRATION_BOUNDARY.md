# M0 Persistence and Orchestration Boundary Proof

> **Gate:** M0-06
>
> **Scope:** prove the stabilized core Stage family can be orchestrated by S12 without changing its domain contracts. This document does not implement the production DAG.

## 1. Persistence identity

One persisted stage execution is identified by:

```text
(run_id, stage_name)
```

with `stage_version` stored on the same `RunStageResult` row.

The canonical mapping is direct:

```text
RunStageResult.stage_name    = Stage.name
RunStageResult.stage_version = Stage.version
RunStageResult.output_fingerprint = str(StageResult.fingerprint)
```

No DB-specific stage enum exists.

## 2. Provenance hashes are intentionally separate

`RunStageResult` owns three different SHA-256 values:

- `input_hash` — resolved execution input identity;
- `config_hash` — resolved stage configuration identity;
- `output_fingerprint` — exact canonical fingerprint emitted by core `StageResult`.

They are not interchangeable.

For S12 checkpoint reuse, the resolved input hash must incorporate the relevant dependency output
fingerprints in a canonical order in addition to other resolved inputs. The current M0 work does
not define the serializer or checkpoint algorithm; that remains S12-T03.

The new `output_fingerprint` column is nullable for pre-stabilization rows and incomplete
executions. New successful stage executions in S12 must populate it.

## 3. Lifecycle ownership

### Core

Core owns:

- typed algorithm input/output;
- Stage name/version/dependencies;
- deterministic StageResult fingerprint;
- non-fatal diagnostics;
- RunContext deterministic semantics.

Core does not own DB/queue lifecycle state.

### RunStageResult

Persistence owns:

- pending/running/succeeded/failed/cancelled/skipped;
- progress;
- stage identity/version;
- input/config/output provenance hashes;
- diagnostics serialization;
- artifact references;
- started/finished timestamps.

A successful GenerationRun remains immutable under the existing DB/model guards.

### Artifact

`ArtifactStore` remains the storage-neutral bytes port.

The DB `Artifact` row remains authoritative for lifecycle:
`temporary -> ready -> referenced -> expired`.

`RunStageResult.artifacts` is the normalized provenance relation. The existing JSON artifact refs
remain a compatibility/read-model field and are not a replacement for the normalized relation.

### Job and outbox

`Job` is the authoritative retryable background-work state.

`JobOutbox` is authoritative enqueue-delivery state. Redis/ARQ is delivery infrastructure, not the
source of job truth. The stable enqueue key is derived from `job_id`, so repeated outbox delivery
is at-least-once without creating a new authoritative job.

## 4. Worker boundary

`worker.tasks.run_generation` is still an explicit S12 placeholder. It validates `run_id` and
returns an acknowledgement but **does not execute the Stage DAG**.

M0 intentionally does not replace it.

S12 must replace that placeholder only after:

1. executable DAG validation exists;
2. persisted context/config assembly exists;
3. checkpoint identity/reuse exists;
4. stage result lifecycle transitions are defined.

## 5. S12 compatibility proof

The stabilized Stage contract does not need new fields for production orchestration.

S12 can provide its own application execution context containing repositories, cancellation,
progress reporting, clock and transaction boundaries while passing only
`TerritorySnapshot + RunContext + typed input + typed config` into core Stage execution.

Therefore S12 can be implemented as an application/worker layer around the existing Stage contract
rather than redesigning Stage or RunContext.

## 6. M0-06 acceptance

M0-06 closes when:

- migration adds dedicated `output_fingerprint` provenance;
- canonical implemented Stage metadata fits the RunStageResult identity contract;
- migration-from-zero CI passes;
- Job/outbox/artifact ownership remains unchanged and existing tests stay green;
- the generation worker is explicitly documented as deferred S12 functionality;
- no production DAG/checkpoint implementation is introduced in M0.
