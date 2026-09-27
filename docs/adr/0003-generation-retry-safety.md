# ADR 0003 — Safe bounded generation attempt retry

> **Status: Accepted**
>
> Date: 2026-09-27

## Context

S12-T06 adds error classification/backoff to the canonical generation worker. A
transient failure is not sufficient evidence that replaying its run is safe:
stage results and generated output are run-scoped and typed checkpoint output
hydration and artifact rollback are not yet implemented. Blindly retrying after
a stage began would either collide with existing `RunStageResult` rows or
silently duplicate stage side effects. Redis/ARQ delivery is not authoritative;
a duplicate delivery must not bypass a scheduled delay. Successful results and
one-way cancellation requests must remain untouched.

## Decision

- Preserve the existing `UrbanGeneratorError` taxonomy: known typed transient
  failures retain `transient.error`; untyped unexpected errors map to
  `permanent.error`; cancellation remains `cancelled.error` and never retries.
- Only a transient failed attempt with **zero persisted stage rows** and remaining
  `Job.max_attempts` is replay-safe. Failure is committed as matching queued
  run/job with the original canonical transient diagnostic and
  `error_json.retryable=true`. A partial attempt, exhausted budget, or
  permanent failure is terminal `failed`, with `retryable=false`; prior
  successful stage rows and their output provenance are never deleted/replayed.
- The current `Job.finished_at` of the last transient attempt, persisted
  `attempt_count`, and deterministic delay of 30, 60, 120, 240, then capped
  at 300 seconds define the next eligible DB claim. An early/duplicate delivery
  receives the remaining delay and does not increment the attempt count.
  The worker translates this signal into ARQ `Retry(defer=...)`.
- Claim/cancellation/failure transitions lock the run and job in the existing
  order. A queued retry may be cancelled immediately and is never re-claimed
  as a fresh attempt after cancellation. Successful runs remain immutable.
- No schema migration is necessary: the existing nullable `Job.finished_at`,
  `error_class`, `error_json` and `attempt_count/max_attempts` encode
  retry eligibility and the DB-enforced delay. The next successful claim clears
  previous errors, and final success retains no transient failure diagnostics.

## Consequences

A safe transient failure during context/config assembly (or input resolution
before any stage row) can be retried through the same canonical run/job within
the attempt budget. A transient failure after a stage has started is classified
as transient but intentionally **not automatically replayed**; the backend
cannot reconstruct typed earlier outputs or prove side-effect/artifact cleanup.
Cross-run restart/resume, worker crash reclamation, outbox recovery, and
orphan artifact cleanup remain separately scheduled work. This is a safety
boundary, not a claim of full stage-level resumability.
