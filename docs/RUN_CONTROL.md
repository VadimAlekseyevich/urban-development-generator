# S12 run control and authoritative progress (UG-AI-079 / S12-T14)

## Contract

The frontend sidebar contains a create/cancel/manual-retry/progress panel for the
currently selected Project and DatasetVersion. The database, not React, Redis,
or HTTP polling, owns GenerationRun, Job, RunStageResult and JobOutbox state.
The run-control API does not execute GIS code or enqueue jobs directly.

- POST /api/v1/projects/{project_id}/runs creates one queued GenerationRun,
  one matching generation_run Job (3 max worker attempts), and one pending
  generation JobOutbox atomically. The input contains canonical mode, signed
  BIGINT-safe nonnegative seed, 1–32 distinct project-owned READY dataset
  version UUIDs, a finite full JSON config object, nonblank config schema
  version (max 64), and a 40-hex lowercase executable code commit SHA.
  The project must have a boundary in its metric working SRID. API checks
  the SHA syntax; deployers must supply the real worker-executable commit.
  No successful processing is implied by HTTP 201.
- GET /api/v1/projects/{project_id}/runs?limit=50 returns 1–100 newest
  project-owned rows, with truncation signal; GET /.../runs/{run_id} returns
  one. A snapshot includes run status, Job status/attempt budget and one-way
  cancel_requested_at, and the persisted progress_percent/status of each
  RunStageResult. An absent legacy Job remains null, not fabricated.
- POST /.../runs/{run_id}/cancel calls the existing
  SqlAlchemyGenerationStateStore.request_cancel. A queued run/job becomes
  cancelled immediately; a running job only gets a DB cancellation request
  and remains running until the worker's cooperative boundary. Repeated
  cancellation is harmless; succeeded/failed/cancelled records are unchanged.
- POST /.../runs/{run_id}/retry is a manual NEW run from a failed/cancelled
  source, with an explicit rerun_source_id and fresh Job/Outbox. It preserves
  source mode/seed/config/version/code/dataset references, checks current
  boundary/SRID/READY project-owned datasets, and never resets the original
  run, alters error diagnostics or reuses output artifacts. A row lock on
  the terminal source plus existing child lookup makes duplicate retry
  submissions idempotent for the same source. This is manual input replay,
  not the S12-T11 successful-run exact rerun with artifact/code availability
  proofs; no claim of those proofs is made here.

422 means invalid request shape/bounds; 404 means missing project or run in
that project; 409 means unavailable inputs or illegal lifecycle state.
The existing JobOutbox dispatcher and worker remain the only queue handoff
and execution path. No migration or second state model is introduced.

## Polling / UI acceptance

The panel uses one typed runApi adapter and a separate useRunPolling hook:
one request in flight; 3-second refresh while any of the 50 visible runs is
queued/running; stop when all visible runs are terminal. Failed requests
retry at 6/12 seconds, then stop after three consecutive errors until
manual refresh. The current AbortController and timer are cleaned on
project change, refresh, or unmount. A manual refresh follows create,
cancel, and retry. The UI displays persisted stage percentages individually;
it does not extrapolate a synthetic overall percent or treat a cancellation
request as terminal completion.

Acceptance: tests/integration/test_run_control_api.py checks committed
run/job/outbox atomic creation, stable scoped reads, queued and running
cancellation, persisted stage display data, idempotent manual retry,
original-run immutability, invalid inputs, foreign-project 404s and no
partial creation on 409/422. Frontend quality gate includes TypeScript
typecheck/build. For an executable worker runtime, deploy the actual code
revision and a supported full stage config; arbitrary placeholder config
is not a success fixture.
