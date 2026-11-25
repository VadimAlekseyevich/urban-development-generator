# ADR 0008 — Verified immutable-input exact rerun

> **Status: Accepted**
>
> Date: 2026-09-27

## Context

S12-T11 needs an exact rerun of a successful generation without altering that run
or inventing cross-run checkpoint reuse. Existing successful `GenerationRun` scalar
inputs and linked DatasetVersion identities are immutable, but a version may have
a legacy null checksum, its source artifact may have expired/disappeared, and a
40-hex commit string does not prove that executable code is still deployable.
Project boundary geometry is a mutable project field, not a versioned run snapshot.

## Decision

1. `SqlAlchemyExactRerunService.create(source_run_id)` accepts only a successful
   source `GenerationRun` and copies its mode, seed, working SRID, full canonical
   finite JSON config and schema version, recorded code commit, and **same**
   ordered DatasetVersion references. It never copies generated entities, stage
   checkpoints, artifacts, metrics or status. Each request creates a distinct
   queued run and records immutable `rerun_source_id` lineage; a DB-level
   self-reference check and `ON DELETE RESTRICT` protect that provenance.
2. Every linked DatasetVersion must remain `ready`, project-owned, and have a
   non-null SHA-256 and canonical source `artifact_key`. The artifact must be
   a `referenced` DB row owned by that version, with the same checksum. Through
   the existing `ArtifactStore`, verify ready object metadata, size and SHA-256
   of actual payload bytes, streaming in bounded 1 MiB chunks. If any blob,
   checksum or ownership proof is missing/inconsistent, fail closed.
   Legacy/unverifiable sources cannot be silently treated as exactly replayable.
3. An explicitly injected `CodeRevisionAvailability` port must affirm that
   the **recorded** code commit is executable in the target environment; syntax
   alone or defaulting to the currently running worker code is insufficient.
   The caller/deployment is responsible for providing a revision-aware runtime.
   The service rejects a missing/unavailable commit rather than changing it.
4. Validate the current project boundary presence/metric SRID. Source
   `GenerationRun` does not persist the original boundary content fingerprint;
   consequently this task guarantees exact cloning of its **recorded** inputs,
   not retrospective proof that a mutable project boundary never changed, nor
   byte-for-byte identical algorithmic output across a changed environment.
   A future durable boundary snapshot/version should close that known gap before
   claiming complete real-territory replay.
5. Validation and insertion are performed transactionally against DB-locked
   provenance rows. New normal `GenerationRun`, `Job` and pending
   `JobOutbox` are inserted with explicit foreign-key flush ordering in a
   single transaction. Redis delivery remains the existing dispatcher concern;
   no new scheduler or cross-run checkpoint hydration is introduced.
   Distinct replay requests create distinct run/job identities.

## Consequences

The DB lineage is durable, successful rows stay immutable, and a replay never
uses an unverifiable legacy checksum or missing source blob. Source blob
verification incurs one sequential read per linked version and holds shared
metadata locks during validation; memory stays bounded. Current project
boundary availability is validated but original geometry identity cannot be
proven from legacy run rows. Provenance manifest/export work remains S12-T12.
