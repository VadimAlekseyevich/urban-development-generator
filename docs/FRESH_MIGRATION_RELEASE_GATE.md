# S14-T13 — Migration-from-zero release integration gate

**Ordered task:** UG-AI-107. This is an additional release gate, not a
replacement for the established unit, migration-transition, S3, browser or
production-like Compose checks.

## Distinct independent CI lane

The `release-migration-from-zero` GitHub Actions job provisions a separate
PostgreSQL 16/PostGIS container and Redis container. It creates database
`urban_generator_release_gate` **from `template0`**; it does not use an
existing app DB, tests' seeded DB, restored DB or production credentials.
The first inspection confirms there are **no** application or Alembic tables.
The second inspection follows `alembic upgrade head` and proves:

- The DB revision is the sole head from the source-controlled Alembic graph.
- Every currently registered SQLAlchemy model table and column is present;
  source/generated, jobs/outbox, stage results, scenario, and both exports are
  separately protected as required release tables.
- The migrated schema has the expected basic CHECK/FK coverage, PostGIS
  functions, both named spatial indexes and the ready-source immutability
  trigger.
- The entire `tests/integration` suite executes against **this newly
  migrated database**. It includes actual upload→worker→ready PostGIS ingest,
  run/job/stage persistence and retry/cancel, idempotent outbox dispatch,
  source and generated layer reads, export pathways, geospatial constraints,
  scenario matrix and protected operational metrics.
- A final Alembic/schema inspection asserts integration tests did not drift
  the revision. Three small JSON reports are uploaded as
  `fresh-release-migration-evidence` for 14 days. No database dump, S3
  object, personal data or connection credentials are uploaded.

CI intentionally uses **one linear migration head** and no schema snapshots
from an existing environment. The CLI `scripts.fresh_migration_gate`
refuses non-disposable database names and asserts the connected database
matches `--expected-database`. Do **not** run it against production. The
integration suite is not a proof that the production generation runtime
generates complete non-synthetic scenarios; that remains a distinct delivery
acceptance condition.

## Reproduction

With local PostGIS 16 and Redis available, run in a disposable environment:

```sh
# Create an isolated DB from template0, then explicitly export DATABASE_URL
uv run python -m scripts.fresh_migration_gate \
  --phase empty --expected-database urban_generator_release_gate \
  --output /tmp/release-empty.json
uv run alembic upgrade head
uv run python -m scripts.fresh_migration_gate \
  --phase migrated --expected-database urban_generator_release_gate \
  --output /tmp/release-schema.json
uv run pytest tests/integration
uv run python -m scripts.fresh_migration_gate \
  --phase migrated --expected-database urban_generator_release_gate \
  --output /tmp/release-posttests.json
```

The script only inspects the configured DB and writes its small evidence
file; it does not create, destroy, truncate or change the DB.

## MinIO/S3 CI reliability

The Python CI job formerly cloned a release of MinIO and built the Go
server from source every run. In CI run
[39482587790](https://github.com/VadimAlekseyevich/urban-development-generator/actions/runs/39482587790)
all application Python tests, frontend, Compose and browser passed, but the
S3 job failed while downloading/compiling MinIO dependencies. This was not
evidence of an S3 adapter contract failure.

The S3 step now runs the **published pinned**
`minio/minio:RELEASE.2025-05-24T17-08-30Z` OCI image and uses the
same health check, test bucket and real adapter contract as before.
It never silently skips the MinIO test when the dependency is unavailable;
the job remains red if the container cannot be started or S3 operations fail.

## Remaining risks

- The release lane duplicates deliberate parts of existing integration
  testing to detect **order and schema assumptions** that can be hidden by
  a warmed fixture DB. It does not replace isolated transient-failure
  worker tests in S14-T14.
- Schema CHECK/FK count thresholds are coarse drift alarms, not guarantees
  of full relational integrity. Existing constraint-specific unit/integration
  tests and migration guards remain authoritative.
- S3 CI exercises a local object store. Production external S3 reliability,
  secret rotation, recovery, performance and security must be separately
  verified; release container images must be pinned and periodically reviewed.
- Failure to create/migrate the isolated DB or a failing integration test
  **blocks merging**; evidence is not a success record until all required
  CI jobs succeed on the exact PR head.
