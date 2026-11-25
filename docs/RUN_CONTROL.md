# S12 run control and authoritative progress (UG-AI-079 / S12-T14)

## Contract

The frontend sidebar contains a create/cancel/manual-retry/progress panel for the
currently selected Project and explicitly selected ready input versions. The
map's inspected DatasetVersion is independent from the 1–32 input selection. The database, not React, Redis,
or HTTP polling, owns GenerationRun, Job, RunStageResult and JobOutbox state.
The run-control API does not execute GIS code or enqueue jobs directly.

- POST /api/v1/projects/{project_id}/runs creates one queued GenerationRun,
  one matching generation_run Job (3 max worker attempts), and one pending
  generation JobOutbox atomically. The input contains canonical mode, signed
  BIGINT-safe nonnegative seed, 1–32 distinct project-owned READY dataset
  version UUIDs, a full typed JSON config for all twelve stages with
  schema version `canonical-stage-configs-v5`, and a 40-hex lowercase executable
  code commit SHA. The worker's canonical resolver validates the config before
  run/job/outbox creation; old or partial configs are rejected.
  The project must have a boundary in its metric working SRID. API checks
  SHA syntax and, when labelled, the executable backend build revision.
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
  run, alters error diagnostics or reuses output artifacts. A new retry validates
  the source config against v5; an existing idempotent child is returned unchanged.
  A row lock on
  the terminal source plus existing child lookup makes duplicate retry
  submissions idempotent for the same source. This is manual input replay,
  not the S12-T11 successful-run exact rerun with artifact/code availability
  proofs; no claim of those proofs is made here.

422 means invalid request shape/bounds or incompatible typed stage config;
404 means missing project or run in
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


## Unconfigured generation worker: terminal failure, not a stuck queue

When the deployed generation worker has **no production
`generation_runtime_factory` in ARQ task context**, it now claims the
DB-authoritative queued run/job and records a **terminal non-retryable
`config.error`** with `stage_name=assembly` and
`component=generation_runtime_factory`, then fails the ARQ task.
It never marks a Stage complete or fabricates a successful scenario. A
duplicate delivery for an existing `succeeded`, `in_progress`, or
`cancelled` run preserves its persisted state.

The guard is retained even though #216 now installs the complete production
runtime. Issue #202 remains open until the real upload-to-generation workflow
is proven without database seeding. The safety guard ensures
failed jobs release their active-project admission slots (S14-T02) and
the UI can display a machine-readable cause instead of polling forever.

Integration regression: `tests/integration/test_generation_worker_e2e.py`
verifies missing-factory terminal DB failure and unchanged successful
run state on duplicate delivery.


## Executable backend revision

Build API and workers from the same clean checkout with:

```sh
export GENERATION_BUILD_REVISION=$(git rev-parse HEAD)
docker compose up -d --build
# Production: use --env-file .env.production -f compose.production.yml
```

The build argument is written to /app/.generation-revision in each backend
image. GET /api/v1/generation/revision exposes the API image's revision;
the browser populates a read-only commit field when present. New manual
runs/retries reject mismatching revisions before enqueue; the actual
generation runtime independently checks persisted SHA against its own
marker before executing stages, including runs queued by other callers.

Production generation fails closed if unlabelled. Local unlabelled
development allows explicit manual SHA but marks it unverified.
The marker is deployment metadata, not cryptographic attestation; build
from a clean checkout and use the same immutable image for API and worker.
Browser CI builds with its actual checkout SHA and verifies mismatch
rejection and successful execution. For local E2E, export the build
revision before building the stack.

## Input compatibility preflight

New create and new manual retry resolve canonical layer families from the
selected ready versions, including bounded source-table probes for OSM.
Before creating any run, Job or outbox they reject:

- fixed roads/buildings/facilities/zones in FROM_SCRATCH;
- fixed zones or population baseline layers (not yet hydrated);
- positive slope without exactly one DEM, positive landuse without landuse,
  positive road_proximity without roads, or unsupported positive factors;
- fixed roads without an attachment policy, and population growth rate
  without implemented baseline hydration.

Input conflicts are HTTP 409; unsupported config choices are HTTP 422.
Existing idempotent retry children remain unchanged. This preflight checks
layer compatibility, not spatial coverage, raster validity, actual artifact
checksums or topology; the worker still performs authoritative reads and
algorithm validation. Code identity is checked separately as described above.
