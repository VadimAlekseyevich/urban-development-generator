# ADR 0002 — DB-authoritative cooperative generation cancellation

> **Status: Accepted**
>
> Date: 2026-09-27

## Context

S12's worker executes canonical bounded stages in a separate process. ARQ/Redis is a
delivery mechanism, not the source of truth, and the existing Job status enum only
contains terminal `cancelled`, not an in-flight cancellation request. Mutating a
running Job directly to `cancelled` while its stage is still executing would create
inconsistent run/stage/job states and race with worker completion. Successful runs
and their stage outputs are immutable.

## Decision

- Add nullable, one-way `Job.cancel_requested_at` as the DB-authoritative signal.
  `SqlAlchemyGenerationStateStore.request_cancel(run_id)` serializes with worker
  transitions by locking the run and its generation Job in that order.
- A queued request immediately transitions both queued run and Job to terminal
  `cancelled` without starting an attempt. A running request only timestamps the
  Job; the worker owns terminal run/job/stage transitions at a safe boundary.
  Repeated requests do not reset the timestamp. Terminal states, especially
  successful runs, are never changed by cancellation requests.
- The application-layer `GenerationStateStore.check_cancelled` port raises the
  existing canonical `CancelledError`; the worker checks before and after each
  bounded stage, including skips and finalization. DB write transactions recheck
  the request under the same lock order to serialize last-moment requests.
- A currently running stage interrupted at a cooperative boundary is marked
  `cancelled` without publishing an output fingerprint. Earlier successful stage
  provenance remains intact. Run/Job become `cancelled` using the existing
  `CancelledError` taxonomy, not permanent failure.
- Core Stage/StageResult and RunContext stay infrastructure-independent. No
  algorithm is forcibly terminated in the middle of an unbounded call.

## Consequences

This supports queued and between-stage cancellation, including a signal received
while a synchronous stage is executing, observed after that bounded execution
returns. A long monolithic algorithm is not pre-empted; internal checkpoints would
need a separately designed core-neutral cancellation port. Cancellation transport
/API exposure, generalized retry/backoff and artifact cleanup remain ordered
later tasks. Same-run successful checkpoint output hydration remains unchanged.
