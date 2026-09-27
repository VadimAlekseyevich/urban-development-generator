# ADR 0006 — Run-owned ScenarioBatch admission and state

> **Status: Accepted**
>
> Date: 2026-09-27

## Context

S12-T09 adds 3–10-run scenario groups while existing `GenerationRun` and `Job`
remain authoritative for an individual execution. A static batch concurrency field
alone cannot prevent simultaneous worker claims from exceeding its value. Requiring
three children on every membership INSERT would also make ordinary transactional
construction of a valid batch impossible.

## Decision

1. `ScenarioBatch` is project-owned persistence/application metadata, not a new
   core Stage or separate job lifecycle. Each child is an existing `GenerationRun`
   with its own `Job` and immutable dataset/config/seed provenance.
2. An ordered `ScenarioBatchRun` association has one globally unique `run_id`
   and a unique 0–9 `position` per batch; a run cannot be shared between batches.
   The PostgreSQL membership trigger enforces matching project, queued child status
   and draft-only modification. A draft may be assembled transactionally; transitioning
   to `queued` requires 3–10 children and `1 <= concurrency_limit <= child count`.
   Membership and concurrency limit freeze after queueing.
3. Before any child `GenerationRun` is locked or claimed, its worker transaction
   locks the parent `ScenarioBatch` row. It then checks the count of `running`
   children against the persisted limit, and only commits the new child and job
   `running` within that same transaction. If at capacity, it raises the existing
   `GenerationRetryScheduled(30)` without consuming the child's attempt budget or
   mutating its queued state; ARQ's existing bounded deferral path carries it.
   Independent (non-batch) runs are unchanged.
4. The parent starts at `queued`, changes to `running` on its first child claim,
   and an explicit idempotent `refresh()` reconciles terminal status from
   authoritative child runs. All children must be terminal: any failure yields
   `failed`, otherwise any cancellation yields `cancelled`, otherwise
   `succeeded`. PostgreSQL validates the terminal aggregate and rejects
   terminal parent mutation. This explicit reconciliation is recoverable after
   a worker crash; parent status does not claim instant real-time push updates.
5. The model does not expand matrix seed/config variants, create a new queue,
   or perform cross-run checkpoint hydration. These remain separately ordered
   S12 work.

## Consequences

The row lock serializes admissions across multiple ARQ workers even when they receive
duplicate deliveries. The indexed unique membership limits a batch to ten without
an unbounded count scan. An explicit queued transition validates the minimum count.
Parent status is a persisted projection of the child states and can be reconciled
repeatedly; child run/job states remain authoritative.

A saturated batch defers queued children instead of claiming a false running slot.
This release adds a creation/reconciliation application boundary but not a general
batch scheduler, API, or seed/config matrix expansion.
