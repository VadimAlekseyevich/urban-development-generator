# S14-T14 — Interrupted worker recovery and reliability boundary

**Work item:** UG-AI-108. The worker, DB stage store, outbox, checkpoint and
artifact-GC contracts remain canonical; this change does not add a parallel
execution runner.

## Why crashed jobs need explicit recovery

An unexpected process exit can happen after the generation job is claimed
(`running`) and after a stage row commits, but before the stage completion
transaction. The next ARQ delivery returns `in_progress`. It **must not**
restart a GIS stage on the same run: partial generated rows/artifacts and
checkpoint hashes might already exist. A timeout alone cannot distinguish a
dead worker from a very slow GIS stage because there is no distributed lease
heartbeat and fencing token.

The recovery operation is deliberately **manual** and always terminates the
old run. It never requeues the same outbox job or replays any stage. Completed
stage fingerprint/provenance survives; an incomplete stage is marked failed,
or cancelled if a cancellation was already requested. No fake success is
published. Stage-free interruptions are also terminal; after investigation,
the user can create a normal **new** retry run through the existing run
control API, never by mutating the old run.

## Operator workflow

1. Stop **all generation worker replicas** and verify none is executing or
   about to resume the target run. Stop any automated redelivery first.
   Redis/ARQ outbox records alone cannot prove the worker is dead.
2. Inspect the job/run and stage rows, confirm the run has no recent activity
   and select a conservative inactivity threshold (at least 30 minutes).
3. Preview the command (does not inspect or alter DB, prints dry-run reminder).
   Then explicitly execute it with the worker-stop acknowledgement:

```sh
python -m worker.recover_interrupted_generation \
  --run-id 00000000-0000-0000-0000-000000000000 --stale-minutes 60

python -m worker.recover_interrupted_generation \
  --run-id 00000000-0000-0000-0000-000000000000 --stale-minutes 60 \
  --confirm-workers-stopped --execute
```

4. Check the run/job terminal `failed` or `cancelled` state, immutable
   successful stage fingerprints, and any interrupted stage diagnostic.
   Restart generation workers only after reconciliation. Perform a new
   project-scoped retry using the established API if desired.
5. Use artifact GC **dry-run** first to identify old unreferenced run blobs,
   then bounded execute after the configured retention threshold. GC never
   reclaims a run's bytes while its run status is `queued` or `running`.

## Invariants and evidence

- Incomplete or recent DB activity blocks recovery. The caller must attest
  that workers are stopped; merely providing an old timestamp is insufficient.
- Existing terminal run/job states are returned idempotently without mutation.
- Duplicate delivery during `running` and after terminal reconciliation
  cannot create a second attempt or replay an already executed stage.
- A hard process-style `BaseException` interruption is injected before
  any stage and during a canonical Stage; tests assert real Postgres job,
  stage and fingerprint results, cancellation and GC through LocalArtifactStore.
- Previously dispatched outbox rows and ARQ deduplication identity remain
  unchanged. Operator recovery never pretends to re-enqueue them.
- This is a **single-run fenced recovery**, not an automatic dead-worker
  detector, production process kill test, or all-queue chaos harness.
  Real SIGKILL/restart acceptance and ingestion/export worker crash recovery
  require independent tests before claiming complete system resilience.

```sh
uv run pytest tests/integration/test_generation_worker_e2e.py \
    tests/integration/test_outbox_dispatcher_recovery.py \
    tests/integration/test_artifact_publication_gc.py
```

Full CI including frontend, migration and Compose remains required before
UG-AI-108 can be marked complete.
