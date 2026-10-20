# S14-T12 — Database backup/restore and external artifact integrity

**Ordered work item:** UG-AI-106. PostgreSQL/PostGIS stores authoritative
project, DatasetVersion, run, stage, job, outbox and Artifact **metadata**.
Actual content addressed by `artifact://…` lives in the configured S3 or
LocalArtifactStore **outside the database**. A `pg_dump` archive can preserve
the URI, SHA-256, lifecycle/owner fields and foreign-key links, **but can never
restore the bytes at those URIs**.

## Required recovery contract

- Back up a consistent Postgres 16 custom-format logical archive using the
  `pg_dump` version matching the server major release. Include all public
  tables, PostGIS extension metadata, Alembic version, triggers and constraints.
  Do not use a schema-only or `--data-only` dump. Do not select only projects:
  immutable input versions, generation-run bindings, stage fingerprints,
  join tables and the outbox must be restored together.
- **Separately** snapshot/version the entire object-store namespace (S3 bucket +
  prefix and lifecycle metadata or local storage root, including sidecar files).
  Protect it from artifact GC, expiry and S3 lifecycle deletions during a
  recovery interval. Retain the S3 snapshot at least as long as the matching
  DB archive. For strong consistency, quiesce API writes, workers, the
  outbox dispatcher, GC and externally triggered uploads before both snapshots.
  PostgreSQL dump snapshots are consistent internally, **not atomic with S3**.
- Archive, copy and encrypt the two backups separately using private backup
  credentials; never put database passwords or S3 secrets into this repo.
  Validate retention, restore rights, available disk and PostGIS version.
- Restore into an **empty, isolated** database using `pg_restore` with
  `--exit-on-error`. Never restore over an active production database; never
  claim an application recovery by simply replaying the database onto an
  unmatched artifact namespace.
- Keep the restored S3 bucket and prefix **identical or intentionally remap
  references with a reviewed migration**. Do not silently regenerate artifact
  keys, checksums or successful StageResult fingerprints.
- Before enabling API/ARQ/dispatcher: verify stored artifact URIs, owners,
  checksums and the backing S3 object bytes, check dataset/run/stage
  relationships, PostGIS geometry and schema revision, and reconcile
  `pending` outbox records/retries. Restore Redis job queues from a
  documented strategy or let the DB outbox/worker claim logic recover them:
  Redis is transport, while PostgreSQL is authoritative.

## Operator example (admin host, no application stack running)

Use environment-based libpq configuration (`PGHOST`, `PGPORT`,
`PGUSER`, `PGDATABASE`, `PGPASSFILE`) and a privileged, restricted
backup directory; replace database names with your real environment. **Never
paste passwords into command-line DSNs or commit them in shell history.**

```sh
# With writes and workers/GC paused, and the S3 prefix snapshotted separately:
pg_dump --format=custom --no-owner --no-acl \
  --file=/secure/backups/urban-metadata.dump

# On a separate recovery host/cluster with the same PostGIS major version:
createdb --template=template0 urban_generator_recovery
PGDATABASE=urban_generator_recovery pg_restore \
  --no-owner --no-acl --exit-on-error \
  --dbname=urban_generator_recovery /secure/backups/urban-metadata.dump
PGDATABASE=urban_generator_recovery psql -c "SELECT version_num FROM alembic_version"
PGDATABASE=urban_generator_recovery psql -c "SELECT postgis_version()"
PGDATABASE=urban_generator_recovery psql -c \
  "SELECT state, count(*) FROM artifacts GROUP BY state ORDER BY state"
```

The restored database is not safe for users merely because these commands
exit with code 0. Validate or restore the **external objects** separately.
In particular, a valid `artifact://runs/.../roads.geojson` record is only a
pointer until the corresponding ArtifactStore blob is present and matches
the persisted checksum. Check `ArtifactStore.stat()` and stream/hash bytes
for sampled and critical artifacts before promoting recovery traffic.
The test fixture below intentionally **does not** manufacture payloads or
pretend such a verification happened.

## Automated CI smoke (database metadata only)

The Python CI job uses the existing local PostGIS service. After migration
tests, `scripts.metadata_restore_smoke prepare` creates a fresh, unique
project with a 3857 MULTIPOLYGON, source road, ready DatasetVersion,
succeeded GenerationRun and StageResult, input-version binding, two
`referenced` Artifact metadata rows, output association, completed Job and
dispatched JobOutbox.

The job uses a **PostgreSQL 16 PostGIS image** as the `pg_dump`,
`createdb` and `pg_restore` client; no dependence on whatever
`pg_dump` major happens to be on the GitHub runner. The restored target is
`urban_generator_restore_smoke`, created using `template0`; the existing
CI development database is never overwritten. `verify` uses a separate
database URL and a random-ID manifest to assert:

1. Project geometry **area and SRID**, source line length, immutable ready
   version checksum and source artifact-key metadata.
2. Run status/commit, bound version, terminal stage status and SHA-256
   fingerprint, stage-output-artifact many-to-many link.
3. **Both** external artifact URIs, SHA-256, `referenced` lifecycle states
   and exact owner UUIDs; no blob is created, copied or restored.
4. Job/outbox dispatch link and queue name, plus Alembic and PostGIS
   availability in the restored database.

The CI job fails closed on missing/changed metadata, any `pg_restore`
failure, or a broken spatial extension. The archive is not published as a
CI artifact because a real database archive may contain sensitive data
even when this particular workflow uses only synthetic fixtures. CI
drops its isolated restored database on exit.

Reproduce locally using a PostgreSQL 16 server and matching client tools:

```sh
uv run alembic upgrade head
uv run python -m scripts.metadata_restore_smoke prepare \
  --manifest /tmp/udg-db-restore-manifest.json
pg_dump -h 127.0.0.1 -U urban -d urban_generator \
  -Fc --no-owner --no-acl -f /tmp/udg-metadata.dump
createdb -h 127.0.0.1 -U urban -T template0 urban_generator_restore_smoke
pg_restore -h 127.0.0.1 -U urban -d urban_generator_restore_smoke \
  --no-owner --no-acl --exit-on-error /tmp/udg-metadata.dump
DATABASE_URL=postgresql+psycopg://urban@127.0.0.1:5432/urban_generator_restore_smoke \
  uv run python -m scripts.metadata_restore_smoke verify \
  --manifest /tmp/udg-db-restore-manifest.json
```

**Scope:** This is an isolated *database-only* regression smoke, not an S3
disaster recovery certification, RTO/RPO measurement, or a claim that the
entire production generator is operational. The 25/100 km² load work and
reliability/crash E2E are separately tracked in S14-T07 / S14-T14.
