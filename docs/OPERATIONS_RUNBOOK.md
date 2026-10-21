# S14-T15 — Operations, troubleshooting and benchmark reproduction

**Status: Draft / UG-AI-109.** This is a read-only operator entry point,
not a replacement for canonical Stage, Job/Outbox, storage, CRS or metric
contracts. Commands are meant for a matching source commit and operator
environment; do not apply production database modifications based on a
synthetic integration fixture.

**Release boundary:** The production-like stack is not public multi-tenant
ready. User authentication/authorization and real production generation
\`generation_runtime_factory\` composition remain independent blockers.
Green CI/browser synthetic fixture or 25/100 km² component benchmarks do
**not** prove a full real-territory scenario or R1–R9 release acceptance.

## Deployment modes and first checks

| Property | Local development | Production-like |
| --- | --- | --- |
| File | \`docker-compose.yml\` | \`compose.production.yml\` (not an overlay) |
| State services | local PostgreSQL/PostGIS 16, Redis, local artifacts | **external** PostGIS 16, Redis and S3 |
| Public ports | API :8000, Vite :5173, dev DB :5432 | nginx :8080 bound to 127.0.0.1; API private |
| Isolation | bind mounts for dev | read-only images, backend UID 10001, nginx UID 101, tmpfs |
| Claim | dev/integration tests | deployment profile, **not** release certification |

Local development (repository root):

\`\`\`sh
cp .env.example .env
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/api/v1/health/live
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
\`\`\`

Production-like (only after provisioning private external PostgreSQL,
Redis, S3 and ingress TLS; never commit a real environment file):

\`\`\`sh
cp .env.production.example .env.production
# Fill external endpoints, credentials and TLS settings privately.
chmod 600 .env.production
docker compose --env-file .env.production -f compose.production.yml config --quiet
docker compose --env-file .env.production -f compose.production.yml up -d --build
docker compose --env-file .env.production -f compose.production.yml ps
curl -fsS http://127.0.0.1:8080/api/v1/health/ready
\`\`\`

Do not redirect unredacted Compose \`config\` output to CI logs: resolved
environment variables contain secrets. Do not open API or DB ports publicly.
The production frontend calls a same-origin nginx \`/api/v1/\` proxy; CORS
allowlisting is not authorization.

\`/health/live\` proves only API liveness. \`/health/ready\` probes PostgreSQL
(\`SELECT 1\`) and Redis (\`PING\`): **not** worker health, S3 read consistency
or an executable canonical generation pipeline. Check \`migrate\` completed
successfully, then the dispatcher and every queue worker. See
[production Compose](PRODUCTION_COMPOSE.md) and
[security audit](SECURITY_AUDIT_S14_T11.md).

## Investigate state before intervening

The following commands target the **local dev** Compose stack. The
production profile deliberately has no \`db\` or \`redis\` service;
connect to managed dependencies through approved private admin channels
instead. Avoid credentials in shell history and uncontrolled logs.

\`\`\`sh
docker compose ps
docker compose logs --tail=100 api dispatcher worker-ingest worker-generation worker-analysis worker-export worker-legacy
docker compose exec -T db psql -U urban -d urban_generator -c \
  "SELECT job_type, status, count(*) FROM jobs GROUP BY 1,2 ORDER BY 1,2"
docker compose exec -T db psql -U urban -d urban_generator -c \
  "SELECT queue_name, status, count(*) FROM job_outbox GROUP BY 1,2 ORDER BY 1,2"
docker compose exec -T db psql -U urban -d urban_generator -c \
  "SELECT status, count(*) FROM generation_runs GROUP BY 1 ORDER BY 1"
docker compose exec -T redis redis-cli ZCARD arq:queue:ingest
docker compose exec -T redis redis-cli ZCARD arq:queue:generation
docker compose exec -T redis redis-cli ZCARD arq:queue:analysis
docker compose exec -T redis redis-cli ZCARD arq:queue:export
docker compose exec -T redis redis-cli ZCARD arq:queue
\`\`\`

PostgreSQL \`Job\`, \`JobOutbox\`, \`GenerationRun\` and \`RunStageResult\`
records are authoritative. Redis sorted-set \`ZCARD\` counts queued
transport items **not** executing processes. The legacy \`arq:queue\`
must be drained by \`worker-legacy\` before it can be retired. Pending
outbox retry preserves the original \`job:<uuid>\` ARQ identity.

| Symptom | Evidence | Safe action |
| --- | --- | --- |
| \`/health/ready\` 503 | DB/Redis connectivity, API logs | Restore failing dependency; do not manipulate Job states |
| 429, \`Retry-After: 30\` | Per-project active Job counts | Respect backpressure and diagnose blocked jobs; never bypass admission |
| Outbox \`pending\` grows | Dispatcher logs, \`next_attempt_at\`, Redis | Restore dispatcher/Redis; let bounded idempotent enqueue retry |
| Redis depth grows | Queue-specific worker status and log errors | Check \`worker-*\` consumer, preserve legacy drain |
| Generation \`config.error\` | Run/Job error \`component=generation_runtime_factory\` | Supply real typed Stage/config/input composition; no fabricated success |
| \`running\` persists after worker crash | Run/Job/stage timestamps and worker-process state | Stop **all** generation workers; operator-fenced terminal recovery |
| Cancel request stays \`running\` | Job \`cancel_requested_at\`; current Stage | Wait for cooperative boundary, or use fenced recovery if worker died |
| Orphan blob | Artifact lifecycle/owner, run status, storage age | Dry-run bounded GC; never delete active run or uploaded inputs |
| Restored DB has missing blobs | URI, checksums, S3 prefix and snapshot | Restore external objects separately; DB-only restore is incomplete |
| Slow benchmark | Code/deps/CPU/RSS, fixture counts/hashes | Compare deterministic work before comparing wall-clock timing |

Do not run ad-hoc \`UPDATE status\`/Redis purge commands to hide backlog.
Manual retry of a failed/cancelled scenario creates a **new** run/Job/Outbox;
it does not reset an immutable terminal source. After a crash, repeat
delivery cannot safely redo a partially written GIS stage. Follow
[generation crash recovery](GENERATION_CRASH_RECOVERY.md): explicit
worker-stop confirmation, inactivity cutoff, terminalization without
replay, and separate orphan cleanup. This recovery remains conditional
on PR #196 being integrated into the deployed commit. A preview does
**not** query or mutate the DB. To execute, first verify all generation
worker replicas have stopped and no old process can resume:

```sh
uv run python -m worker.recover_interrupted_generation \
  --run-id 00000000-0000-0000-0000-000000000000 --stale-minutes 60
# Only after checking the actual run ID and stopping *all* workers:
uv run python -m worker.recover_interrupted_generation \
  --run-id 00000000-0000-0000-0000-000000000000 --stale-minutes 60 \
  --confirm-workers-stopped --execute
```

Never use the example zero UUID on a real system. A stale timestamp
and explicit CLI flags cannot independently prove a process is dead.

Internal \`/internal/metrics\` is disabled by default (404); a distinct
32+ character token enables private bearer access (401 invalid token,
503 if scrape DB/Redis fails). Values are aggregate **gauges**, including
ARQ sorted-set depth, not real worker heartbeats or monotonic counters.
See [operational metrics](OPERATIONAL_METRICS.md),
[worker queues](WORKER_QUEUES.md) and
[project job admission](JOB_ADMISSION.md).

### Artifact cleanup: inspect first

\`\`\`sh
uv run python -m scripts.artifact_gc --max-batch 20 --max-scan 5000
# Only after operator review and verifying run/publisher safety:
uv run python -m scripts.artifact_gc --execute --max-batch 20 --max-scan 5000
\`\`\`

CLI dry-run is always default, even when hourly analysis-worker GC is
configured to delete. Active \`queued\`/\`running\` run blobs remain
protected even if aged; deletion rechecks owner/age and stays bounded.
See [artifact GC policy](ARTIFACT_GC_POLICY.md).

## Upgrade, rollback, restore and release gates

1. Record the deployed commit SHA and **all required CI results for that
   exact head**: Python lint/mypy/tests, migration transitions, isolated
   fresh-PostGIS integration, frontend, browser, Compose and S3 smoke.
2. Keep dispatcher and isolated ingest/generation/analysis/export/legacy
   workers compatible. The old legacy queue cannot be consumed by a
   newly named-queue-only deployment; do not purge Redis.
3. For schema/storage changes quiesce API writes, workers, dispatcher and
   GC, then back up PostgreSQL **and independently** S3/local artifacts.
   Database dump restores only external artifact URIs and checksums,
   not the actual bytes. Verify bytes/checksums after isolated restore.
4. Run the fresh-migration check only on a disposable database beginning
   \`urban_generator_release_gate\`; never reuse a production schema.
5. Rollback must consider Alembic compatibility, queue consumers, persisted
   provenance and external S3. \`docker compose down\` is not a database
   downgrade or object-store rollback.
6. Confirm S3 reads, health, metrics, worker logs, pending outbox, frontend
   and unresolved real-data acceptance blockers before restoring traffic.
   This rehearsal alone does not authorize Internet deployment.

Canonical owners:
[database backup/restore](DATABASE_BACKUP_RESTORE.md),
[fresh migration release gate](FRESH_MIGRATION_RELEASE_GATE.md),
[ingest submission](INGEST_SUBMISSION.md),
[run control](RUN_CONTROL.md), and
[browser E2E limitations](PLAYWRIGHT_E2E.md).

## Reproduce measured reference profiles

All commands start at repository root after \`uv sync --frozen --dev\`.
Only the SQL profile requires a running migrated PostGIS database; the
other workloads use synthetic deterministic component fixtures. Each
territory profile is a separate Python process to avoid cumulative
high-water RSS effects.

\`\`\`sh
uv run alembic upgrade head
uv run python -m benchmarks.db_query_plans \
  --rows 10000 --output /tmp/udg-db-query-plans.json
uv run python -m benchmarks.raster_memory_reference \
  --width 2048 --height 2048 --tile-size 256 \
  --gdal-cache-mb 64 --output /tmp/udg-raster-memory.json
uv run python -m benchmarks.road_hotpath_profile \
  --grid-sizes 8 16 32 --repetitions 2 \
  --snap-queries 128 --path-queries 32 \
  --output /tmp/udg-road-hotpath-profile.json
uv run python -m benchmarks.placement_hotpath_profile \
  --building-counts 1000 3000 --infrastructure-scales 32x8 64x16 \
  --repetitions 2 --output /tmp/udg-placement-hotspot-profile.json
uv run python -m benchmarks.territory_composite_profile \
  --area-km2 25 --output /tmp/udg-territory-25km2.json
uv run python -m benchmarks.territory_composite_profile \
  --area-km2 100 --output /tmp/udg-territory-100km2.json
\`\`\`

Record exact SHA, Python/OS/CPU/dependency versions, raw JSON, input size,
deterministic work counters/hashes and peak-RSS methodology. The SQL
profile includes actual SQL/bind values: **protect its JSON evidence**.
Elapsed timings and CI hardware memory measurements are diagnostic,
not production throughput or availability SLOs.

| Profile | CI artifact (14 days) | Interpretation |
| --- | --- | --- |
| Repository SQL/PostGIS | \`postgis-query-plans\` | 10k-row synthetic owners, transaction rollback |
| Raster windowing | \`raster-memory-profile\` | normalization windowed; suitability still has dense-grid arrays |
| Graph noding/snap/routing | \`road-hotpath-profile\` | synthetic grid; deterministic topology/route counters |
| Building and infrastructure | \`placement-hotspot-profile\` | synthetic demand/cache; no whole pipeline rerun |
| Composite projected 25/100 km² | \`territory-composite-25-100\` | components share workload scale but **do not pass results stage-to-stage** |

Canonical measured-contract details:
[SQL plans](DB_QUERY_PROFILING.md),
[raster memory](RASTER_MEMORY_PROFILING.md),
[road hotspots](ROAD_HOTPATH_PROFILING.md),
[placement hotspots](PLACEMENT_HOTSPOT_PROFILING.md) and
[territory profiles](TERRITORY_COMPOSITE_PROFILING.md).

## Validate the documentation contract

\`\`\`sh
uv run python -m scripts.check_operations_docs
uv run pytest tests/unit/test_operations_runbook.py
uv run ruff check .
uv run mypy backend core worker
uv run pytest
\`\`\`

The checker validates relative document links, actual CLI modules/options,
production service names and Redis queue strings **without** executing any
mutation, database query or network request. Full required CI is still
necessary before accepting this documentation.

Known limits: production runtime composition, authentication, real-territory
end-to-end acceptance, independent S3 recovery, crash tests using real
SIGKILL on all worker roles, and release R1–R9 remain separate tasks.
