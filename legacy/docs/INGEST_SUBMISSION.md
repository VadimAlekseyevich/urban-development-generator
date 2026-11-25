# Real uploaded-Artifact ingestion through the existing worker

This closes the missing UI/API scheduling link between the S13 upload transport
and the S03 canonical ingest worker. The worker still owns the only transition
to `DatasetVersion.status = "ready"`.

## User / API flow

1. Create a project with a metric working SRID, such as EPSG:32637.
2. Upload source bytes to `POST /api/v1/uploads`. The response contains
   `artifact_id`, `checksum` and the canonical ArtifactStore reference.
3. Bind the uploaded artifact to one immutable project DatasetVersion:

```http
POST /api/v1/projects/{project_id}/ingests
Content-Type: application/json

{"artifact_id":"<uploaded-artifact-uuid>","dataset_kind":"roads","source_format":"geojson"}
```

Response (201) includes `project_id`, `dataset_id`,
`dataset_version_id`, `job_id`, `status` (initially `uploaded`),
`job_status` (initially `queued`), `attempt_count`, `max_attempts`,
`retry_pending` and persisted error metadata.

4. The operation inserts Dataset + DatasetVersion + Job + JobOutbox in
   **one PostgreSQL transaction**, using `queue_name = "ingest"` and the
   unchanged deterministic `dataset-version:<uuid>` job key.
5. The dispatcher sends the task to `arq:queue:ingest`; the normal ingest
   worker reads the immutable manifest, verifies ArtifactStore checksum,
   normalizes features/rasters and commits only a complete, ready version.
6. Poll `GET /api/v1/projects/{project_id}/ingests/{dataset_version_id}`;
   only a real `ready` status enables `Выбрать` on the map. The React UI
   reports persisted errors and continues polling retryable failures.

Supported source formats: GeoJSON, GeoPackage, ZIP/Shapefile, GeoTIFF.
Valid vector dataset kinds are roads/buildings/landuse/water/facilities/
constraints; `dem` requires GeoTIFF. A named `layer` may be provided only
for GeoPackage. OSM PBF has a separate import pipeline and is **not** silently
accepted as an ingest format.

## Idempotency, ownership and failure safety

- The submission locks the project and uploaded Artifact row.
- Repeating the **same** project/format/kind/layer submission for the same
  artifact returns the original version and job; no duplicate Job or version.
  Rebinding the same artifact to a different project or manifest returns 409.
- Missing project/artifact and cross-project reads return 404. Unsupported
  formats and kind combinations return 422.
- The source remains a ready, unowned Artifact until the ingest worker claims
  it in its final transaction. `ready` versions and their source rows
  remain immutable; the worker records failed and retryable states.
- No request handler runs GDAL, changes a version to ready or replays the
  source upload.
- This is **project-scoped referential ownership**, not user authentication.
  An internet-facing deployment still requires access control. It remains a
  separate P1 security gate.

## Verification

`tests/integration/test_ingest_submission_api.py` exercises real HTTP upload,
SQLAlchemy/JobOutbox creation, idempotency, cross-project rejection, the
normal production `DatasetIngestPipeline`, PostGIS normalization and ready
Artifact ownership. `frontend/e2e/ingest.spec.mjs` starts with a genuine
browser multipart upload, queues a worker job, waits for the actual ready
DatasetVersion and verifies a named road through the production source API.
Unlike the original comparison/metrics browser fixture, this test does not
seed a version or mock application endpoints.

**Out of scope:** setting a project boundary and constructing the complete
production generation Stage DAG. Run generation currently requires additional
runtime composition (release issue #178); completing ingest does not imply a
successful generation worker run.
