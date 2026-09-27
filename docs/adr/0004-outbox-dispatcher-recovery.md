# ADR 0004 — Restartable DB-authoritative outbox dispatch

> **Status: Accepted**
>
> Date: 2026-09-27

## Context

S02-T07 established one durable `JobOutbox` row per `Job`, due-message selection
with `FOR UPDATE SKIP LOCKED`, a stable `job:<job_id>` queue identity and an
at-least-once delivery port. It did not run a background recovery process or bind
the Redis adapter to the current ARQ worker configuration. A message may be accepted
by Redis and left pending when the dispatcher crashes before its DB acknowledgement.
Two dispatchers must not concurrently enqueue the same due row. Redis queue state
must never replace the DB-authoritative `Job` and `GenerationRun` lifecycles.

## Decision

- Run a dedicated, restartable dispatcher process, not an HTTP request or a core
  algorithm. On startup and on bounded polling passes it claims due `pending`
  outbox rows in deterministic order, with a maximum 500 and default 25 per
  transaction, using the existing `FOR UPDATE SKIP LOCKED` query.
- Keep each selected row locked until its bounded Redis enqueue completes and the
  same PostgreSQL transaction records success or a future exponential retry.
  This deliberately holds a DB row lock over the external call; the concrete
  adapter caps each enqueue wait at five seconds and bounds the batch size.
  A transient error rolls the row forward to a `pending` due retry, not
  `dispatched`. No separate `in_flight` lease or queue-authority status is introduced.
- The Redis adapter validates/allowlists `run_generation` and `run_ingest` payloads.
  It uses ARQ `_job_id=job:<job_id>` on every attempt; an ARQ duplicate-ID
  (null result) is acknowledged as accepted. The existing generation/ingest DB
  claim gates must still handle repeated delivery if the ARQ key has expired.
- Existing logical `default`, `generation` and `ingest` outbox queue names
  all route to the currently configured single ARQ `arq:queue`; separation of
  physical worker queues remains S14-T01. One dispatcher process is started in
  Compose; additional replicas are safe due to the DB claim query.
- All pending outbox rows are rediscovered on process restart. A crash after Redis
  accepted a message but before DB commit leaves it pending and may repeat
  delivery with the same key; missing job-outbox production paths are not
  invented by this task.

## Consequences

The source of truth remains PostgreSQL. A dispatcher can be stopped/restarted
without losing due pending messages, transient Redis faults receive bounded
backoff, and competing processes do not claim the same currently locked row.
Delivery is intentionally at-least-once, not distributed exactly-once. Long or
stuck Redis calls must be bounded by the adapter timeout. The contract neither
retries unsafe partial generation stages nor claims full worker-crash recovery,
typed checkpoint hydration, or future S14 queue partitioning.
