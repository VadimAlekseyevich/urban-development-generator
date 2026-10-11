# S14-T02 — Project-scoped job admission and backpressure

All background-job submitters share **one** PostgreSQL-aware admission policy
before inserting any new Job / JobOutbox state. This protects a single tenant
project from unlimited queued work, including scenario matrices that would
otherwise bypass one-run-at-a-time limits.

## Resource bounds and owners

| Resource | Existing or new authoritative bound |
| --- | --- |
| Queued/running jobs **across a project** | `MAX_ACTIVE_JOBS_PER_PROJECT` (default 16) |
| Queued/running generation jobs **per project** | `MAX_ACTIVE_GENERATION_JOBS_PER_PROJECT` (default 10) |
| Queued/running import jobs **per project** | `MAX_ACTIVE_INGEST_JOBS_PER_PROJECT` (default 4) |
| Queued/running GeoJSON + GeoPackage export jobs **combined** | `MAX_ACTIVE_EXPORT_JOBS_PER_PROJECT` (default 4) |
| Concurrent actual ARQ jobs | Separate `WORKER_*_MAX_JOBS` per worker process (S14-T01) |
| Raw upload bytes | Existing `MAX_UPLOAD_SIZE_MB` byte-enforced streaming reader (1–10240 MiB configurable) |
| Run source versions | 1–32 distinct ready versions (existing run-control contract) |
| Scenario matrix | Existing 3–10 children; **all** proposed jobs count against the same project/role budget atomically |
| Road candidate expansion | Canonical `CandidateRoadAnchorPolicy.max_candidates/max_sampled_cells` and `LeastCostConnectorPolicy.max_visited_cells` |
| Building candidates | Canonical bounded `BuildingPlacementCandidatePolicy`/stage limits |
| Raster grids | Canonical `SuitabilityStageConfig.max_cells` and windowed raster normalizer |
| GeoJSON / GeoPackage exports | Existing feature-count/layer-count caps and paged streaming |

All four `MAX_ACTIVE_*` settings are validated as integers 1–128.
Task classes are allowlisted; an unknown job type never silently shares an
unbounded group.

## Atomic admission

The four mutation families use `SELECT projects … FOR UPDATE` **before**
querying active jobs or writing new jobs:

- `SqlAlchemyRunControlService.create/retry`
- `SqlAlchemyIngestSubmissionService.create`
- `SqlAlchemyGeoJsonExportRepository.create` and
  `SqlAlchemyGeoPackageExportRepository.create`
- `SqlAlchemyScenarioBatchStore.create_matrix`, including **all** 3–10
  child Jobs at once; its project lock is now exclusive.

Job counts come from **only** authoritative `jobs` rows in `queued` or
`running` status. Terminal cancelled/failed/succeeded jobs release capacity.
The project row lock serializes concurrent submitters from different endpoints,
so two requests cannot both allocate the same last slot.

**Idempotency:** for ingest and exports, the existing matching Job is returned
*before* capacity checks. The same request remains replay-safe even when the
project has reached its limit. An accepted transaction writes all associated
run/version/job/outbox rows or none.

When admission is exhausted, HTTP responds:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 30
Content-Type: application/json

{"detail":"active generation jobs have reached the configured limit of 10",
 "scope":"generation","limit":10}
```

The client may retry later, but the server does **not** spin on Redis or
silently discard queued work. Direct internal matrix creation raises
`JobCapacityExceeded` instead of inserting a partial matrix.

## Limitations and acceptance

- This is **per-project admitted pending load**, not total cluster-wide
  memory/CPU admission. Worker process concurrency remains independently set.
- Legacy pre-existing jobs count towards saturation, including jobs that are
  stuck queued because of unfinished production Stage runtime composition.
  These jobs must be explicitly cancelled/resolved, not deleted to mask load.
- Producer project locks are mandatory. Adding a new Job producer must acquire
  the same lock before `enforce_project_job_capacity`; this is an architecture
  change rule, not an optional optimization.
- Candidate/grid/run-matrix per-operation caps remain owned by their typed
  algorithms/validators, not a new competing planner vocabulary.
- The runtime worker composition blocker #178 is **not fixed** by admission.

Mandatory regression evidence: `tests/integration/test_job_admission_api.py`
checks 429 + `Retry-After`, atomic no-partial creation, per-project isolation,
cancelled job slot release, combined export queue budget and idempotent ingest
replay when the ingest role is full. Existing worker/matrix/upload boundary
tests and Compose/Playwright CI remain required.
