# S14-T01 — Isolated worker queues and concurrency

**Scope:** UG-AI-095. The database Job / JobOutbox rows remain authoritative;
the task dispatcher is still at-least-once and preserves `job:<uuid>` ARQ
identity for retries and recovery. Only Redis transport queues and worker
deployment change. No new Stage, Job or analysis task type is introduced.

## Routing

| Logical outbox task | Redis transport | Worker | Default max_jobs |
| --- | --- | --- | ---: |
| `run_ingest` | `arq:queue:ingest` | `worker-ingest` | 2 |
| `run_generation` | `arq:queue:generation` | `worker-generation` | 1 |
| Future analysis outbox jobs | `arq:queue:analysis` | `worker-analysis` | 1 |
| `run_geojson_export`, `run_geopackage_export` | `arq:queue:export` | `worker-export` | 1 |
| Old queued jobs only | `arq:queue` | `worker-legacy` | 1 |

The analysis queue has no user-facing analysis task implemented yet. Its worker
runs the existing **hourly** orphan-artifact GC cron, exactly once among the
active workers. No export/generation/ingest task is registered on it.

The Python port `worker/queues.py` is the **single** mapping. It checks
the allowlisted task against the logical outbox queue and rejects inconsistent
pairs. Historic pending DB outbox rows named `default` are routed by their
actual task to the matching **new** Redis queue. `arq:queue` is preserved
only for jobs enqueued before this deployment; the legacy worker drains it,
and **no new messages** enter it.

Set `WORKER_INGEST_MAX_JOBS`, `WORKER_GENERATION_MAX_JOBS`,
`WORKER_ANALYSIS_MAX_JOBS`, and `WORKER_EXPORT_MAX_JOBS` in `.env`.
Each is validated as an integer between 1 and 32 at process startup.
These are per-worker-process limits; increasing the number of replicas also
increases aggregate concurrency. Defaults deliberately limit the expensive
generation workload to **one** active job per process.

## Upgrade and rollback

1. Deploy new API, dispatcher, and all four queue-specific workers together.
   Do not run the new dispatcher with only the old shared worker, or newly
   enqueued tasks will have no consumer.
2. Keep `worker-legacy` running until the **old** `arq:queue` has drained,
   including its in-progress/retrying jobs. Do not purge Redis during upgrade.
3. Existing `job_outbox` identities are unchanged. If a dispatcher crashes
   after Redis accepted a task, re-enqueue uses the same deterministic
   identity; duplicate receipts are normal and handled by persisted services.
4. Inspect `arq:queue:*` depths and the legacy queue before disabling
   the legacy consumer in a future change. Never drop the old queue blindly.
5. Rolling back the application to an old shared-worker image **cannot**
   consume new named queues. A rollback requires keeping the new queue workers
   alive or a controlled, idempotent migration/drain of those Redis queues.

## Known limits

- Queue separation is **not** cluster-wide admission control or backpressure:
  that is ordered task UG-AI-096 / S14-T02.
- The synchronous dispatcher retains a DB transaction while it awaits up to
  5s per enqueue; under congestion this can hold locks and needs load testing.
- The ordinary generation worker currently has **no
  `generation_runtime_factory` injection**. `run_generation` raises
  `GenerationTaskConfigurationError` without an override. Queue separation
  does not repair this functional blocker.
- CORS is not authentication; a production deployment still needs audited
  access controls and least-privilege policies.
- Redis queue depth metrics, fairness and crash/retry E2E remain S14-T08 and
  S14-T14 respectively.

Regression gates include strict unit route/worker-role tests, actual Redis
queue isolation plus duplicate IDs, the existing outbox transaction recovery
tests, Python integration suite, Compose smoke and the browser E2E test.
