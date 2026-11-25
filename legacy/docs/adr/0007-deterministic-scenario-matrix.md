# ADR 0007 — Deterministic ScenarioBatch matrix creation

> **Status: Accepted**
>
> Date: 2026-09-27

## Context

S12-T09 established a project-owned sealed `ScenarioBatch` of 3–10 existing
`GenerationRun` children with DB-authoritative concurrency admission. S12-T10
needs reproducible config/seed combinations without inventing a second worker
scheduler or submitting half-created batches to Redis.

## Decision

1. `ScenarioMatrixSpec` is an application-level, bounded Cartesian product of
   explicit unique non-negative seeds and named **full** canonical JSON configs,
   with an exact persisted code commit SHA and schema version. The product must
   contain 3–10 children. Seeds fit the signed PostgreSQL BIGINT range as well
   as the core seed range; there is no implicit overflow, randomized derivation,
   partial patch/merge or floating-point NaN.
2. Matrix position order is canonical: variant names ascending and, within each
   variant, seeds ascending. JSON object keys are recursively sorted with
   compact finite JSON encoding. Duplicate variant names and identical full
   configuration snapshots are rejected. Config values are copied by canonical
   serialization; mutating a caller-supplied mapping cannot change expansion.
3. `SqlAlchemyScenarioBatchStore.create_matrix()` preflights the existing
   project/boundary CRS and all selected ready, project-owned DatasetVersions,
   then inserts each normal queued `GenerationRun` (full config, seed,
   dataset-version references, mode and commit), its ordinary generation
   `Job`, and one pending `JobOutbox`. It seals their parent batch through
   the same membership/status transition as manual batch creation in **one
   PostgreSQL transaction**. Failure at any point rolls all child/batch/queue
   metadata back. The existing recovery dispatcher enqueues committed outbox
   rows separately; Redis is not invoked within creation.
4. Determinism applies to semantic run inputs and ordered matrix positions.
   Each creation creates a distinct batch and new run UUIDs: repeating a spec
   is a new experiment, not implicit cross-run reuse. Each generated child
   remains governed by the S12-T09 concurrency/admission mechanism.

## Consequences

No new database schema, stage model, scheduler or queue is needed. Canonical
provenance is stored in the already-authoritative child runs, ordered membership
and outbox rows. Matrix labels determine ordering; the full child config, not
a label/patch, is persisted for execution. A distributed batch scheduler,
HTTP controls, exact rerun and cross-run checkpoint reuse remain later tasks.
