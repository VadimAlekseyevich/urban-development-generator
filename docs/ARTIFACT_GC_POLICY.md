# S14-T09 — Artifact GC dry-run, expiry and orphan safety

**Backlog:** UG-AI-103. Extends the *Accepted* stage-artifact
publication/GC contract in [ADR 0005](adr/0005-stage-artifact-publication-gc.md).
No new Artifact lifecycle state, storage namespace or owner vocabulary is added.

## Operator commands

Preview candidates and protect every byte (default):

```sh
uv run python -m scripts.artifact_gc --max-batch 20 --max-scan 5000
```

Delete only after reviewing the dry-run and ensuring workers/publishers have
finished or are healthy:

```sh
uv run python -m scripts.artifact_gc --execute --max-batch 20 --max-scan 5000
```

The CLI is **dry-run even if the hourly worker is configured to delete**;
`--execute` is the only explicit deletion request. Both commands use the
same configured local or S3 ArtifactStore, PostgreSQL connection, ownership
checks, age gate and bounded scan. They print only aggregated JSON counts,
**never** run IDs, project IDs, artifact keys or storage credentials.

Worker settings (validated by Pydantic):

| Variable | Default | Allowed |
| --- | ---: | ---: |
| `ARTIFACT_GC_DRY_RUN` | `false` | true/false |
| `ARTIFACT_GC_MIN_AGE_HOURS` | 2 | 1–720 |
| `ARTIFACT_GC_MAX_BATCH` | 50 | 1–500 |
| `ARTIFACT_GC_MAX_SCAN` | 5000 | 1–100000 |

The analysis worker's existing **hourly GC cron** uses these settings.
Set `ARTIFACT_GC_DRY_RUN=true` for a read-only deployment audit and flip to
`false` only when actual cleanup is approved. No new cron or competing
worker task is registered.

## Scope and invariant checks

- Only namespace `runs/<canonical-uuid>/stages/<canonical-stage>/<file>`
  can be considered. Uploads, referenced artifacts, non-run-scoped objects,
  recent objects and linked stage-result artifacts **cannot** be deleted.
- Both DB `created_at` and `updated_at` must be older than the cutoff.
  Stored payload/sidecar/S3 modification age is independently rechecked.
- **A run whose authoritative status is `queued` or `running` protects
  even an old, unpublished stage blob**, whether or not the Artifact DB row
  was committed. A terminal run or a no-longer-existing run permits regular
  age-gated cleanup. A perpetually `running` orphan needs recovery of its
  run/job state first; this GC never guesses that it is safe to delete.
- The DB pass locks candidate Artifact rows with `FOR UPDATE SKIP LOCKED`.
  Existing referenced/owner/link invariants are checked while locked.
  Storage-only candidates are individually checked for a matching locked
  Artifact row before any deletion.
- One pass can reclaim no more than `max_batch` combined eligible DB rows
  and storage-only objects, and scans no more than `max_scan` storage
  entries through the existing bounded local/S3 cursors. The DB selection is
  also limited to `max_batch`.
- Dry-run uses the same predicates and capacity budget, but never calls
  `ArtifactStore.delete()` and never transitions any Artifact row. Dry-run
  **advances the in-process storage-scan cursor**, so consecutive scans may
  report different candidates; use a fresh process to repeat a full scan.
- A final storage age recheck immediately precedes each deletion. If the
  object ceased to be stale, its metadata is **not** marked expired.
  External storage deletion and DB commit are not atomic; retries are
  idempotent and may be needed after partial failures.

### Result fields

`expired_rows` and `untracked_keys` count actual changes (both zero for
dry-run). `eligible_rows` and `eligible_untracked_keys` show planned
reclaims, with **combined eligible count capped by max_batch**.
`protected_active` is the number of candidates skipped because their
run is queued or running. `examined_candidates` is the number of stale
storage candidates returned, **not** total directory/S3 entries scanned.
No per-owner cardinality or bytes-deleted counter is guessed.

The existing hourly job returns these fields to ARQ. Use the token-guarded
S14 operational metrics endpoint for aggregate queue/job/status monitoring.

## Acceptance and limitations

`tests/integration/test_artifact_publication_gc.py` covers published
immutable artifacts, recoverable publication, aged metadata vs. storage-only
orphan cleanup, dry-run zero-mutation, active-run retention, age boundaries,
deduplication, bounded batches, and invalid policy flags. CI still runs all
Python/frontend/Compose/Playwright gates.

This GC does **not** expire uploaded READY user datasets, archive completed
runs, or perform global S3 bucket lifecycle policies. It does not replace
separate retention requirements for those namespaces, nor does it infer that
a missing run record is a live publisher. Release security/ops hardening and
crash/retry E2E remain later S14 tasks.
