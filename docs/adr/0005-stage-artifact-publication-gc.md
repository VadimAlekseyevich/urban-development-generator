# ADR 0005 — Recoverable stage artifact publication and bounded orphan cleanup

> **Status: Accepted**
>
> Date: 2026-09-27

## Context

The canonical core `ArtifactStore` has `temporary` and `ready` blob visibility
states, while the existing PostgreSQL `Artifact` model owns the distinct
`temporary`, `ready`, `referenced`, and `expired` lifecycle and its
`run_stage_result_artifacts` ownership relation. Local storage promotion moves
payload and metadata separately and can recover interrupted moves. Neither the
filesystem nor a future S3 adapter shares a transaction with PostgreSQL.
Uploading source artifacts and ingest ownership already use these same types,
but S12 needed stage-result publication plus restartable cleanup.

## Decision

1. A stage-owned publication uses a unique logical key under
   `runs/<run UUID>/stages/<canonical stage name>/...`. The producer first
   writes via canonical `ArtifactStore.put()` and passes the returned temporary
   `ArtifactStat` to `SqlAlchemyStageArtifactPublisher.publish()`.
2. The publisher checks that the run and stage are currently running, validates
   storage checksum/size/content type, and commits one unowned `temporary`
   Artifact row before external promotion. A retry with the same key and exact
   metadata continues an existing temporary row; mismatched metadata/ownership,
   terminal run/stage and changing ready blob content are rejected. There is no
   duplicate byte write on retry.
3. The publisher promotes the blob outside the final DB transaction and may
   complete an interrupted two-file move. In **one** PostgreSQL transaction, it
   locks run, stage and artifact in that order, validates current state/content,
   flushes `temporary -> ready`, then flushes
   `ready -> referenced` with `owner_type=run_stage_result`, `owner_id`
   and the canonical relational stage/artifact link. Rollback leaves an unowned
   temporary DB row for retry/reconciliation. A fully published same-stage call
   is idempotent while the stage remains running.
4. Existing successful-run immutability guards remain authoritative; the
   publisher does not mutate completed stages/runs or invent an output
   fingerprint. Stage-level execution invokes publication while its stage row is
   `running`, before the orchestration marks the stage completed.
5. `SqlAlchemyArtifactGc` executes as an hourly ARQ cron task on the existing
   worker. Each pass is age-gated (minimum one hour, default two), has an
   explicit DB row batch and local directory-scan budget, uses PostgreSQL
   `FOR UPDATE SKIP LOCKED`, and rechecks age/state while locked. It expires
   only aged, unowned, unlinked run-stage temporary/ready metadata and deletes
   their temporary/ready bytes. A local-only bounded scan under `runs/`
   also finds older payload-only, sidecar-only and unregistered orphans left by
   process crashes. It never traverses symlinks, enters `uploads/`, or deletes
   referenced/recent artifacts. No unbounded full-storage listing enters the
   storage-neutral `ArtifactStore` protocol.
6. Byte deletion is idempotent but not part of the DB transaction. Any error
   rolls the DB lifecycle change back for a later pass. A race in which a
   publisher crashes before its final transaction may leave orphan bytes,
   which a later age-gated scan can reclaim; the publisher must not assume
   storage promotion constitutes durable DB ownership.

## Consequences and limitations

No distributed exactly-once filesystem/DB transaction is claimed. A failed
publication may retain an unowned temporary metadata row, even with a ready
physical blob; retry or GC handles it. Production stage composition must call
the publisher with its explicit run/stage identity; merely returning a core
`StageResult` does not fabricate artifact ownership. GC covers the new
run-stage namespace only: legacy uploaded unreferenced ready data and other
storage providers need their own ownership/reconciliation policies. Bounded
scans limit work per pass, so very large backlogs require repeated hourly runs.
S13 storage adapter parity extends this accepted lifecycle without imposing
filesystem enumeration on the canonical port.
