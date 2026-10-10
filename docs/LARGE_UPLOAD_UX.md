# S13-T12 · Large-upload UX and dataset-version discovery

**Scope:** UG-AI-092. The browser uploads a raw `File` as
`multipart/form-data` to the existing `POST /api/v1/uploads` endpoint.
There is no base64 encoding or browser-side full-file buffering/conversion.
The backend enforces its configured upload byte limit and streams into the
selected Local/S3 ArtifactStore, hashes, promotes and persists metadata.

## UI transport contract

- XMLHttpRequest upload progress reports uploaded bytes and percentage when
  the browser knows the transfer length. An unknown length is indeterminate.
- After request bytes are sent, the UI explicitly switches to a server
  processing state: 100% upload does **not** mean persistence succeeded.
- The displayed success is based only on an HTTP success carrying ready
  `artifact_id`, logical key, stored size and checksum.
- Users can cancel the current XHR or manually retry. **No automatic replay**:
  a network failure may occur after the server persisted an artifact, and the
  upload API does not provide idempotency keys. Retrying may create a second
  ready artifact. HTTP 413 identifies the server-side size limit.
- Errors and status are available via live regions. Component unmount cancels
  the in-flight request and discards stale completions. Changing files clears
  stale completion/error metadata. The client does not pretend transport
  cancellation guarantees server-side rollback.

## DatasetVersion is not an Artifact

`POST /uploads` registers a ready, unowned Artifact; it does **not** create
`DatasetVersion`, start ingestion, or bind it to a Project. This feature
deliberately does not fabricate a dataset version or switch the map to a
nonexistent one.

`GET /api/v1/projects/{project_id}/dataset-versions?limit=20&offset=0`
provides read-only, project-scoped version discovery from canonical Dataset
and DatasetVersion rows. Limits are 1–50; offset is non-negative. Order is
newest `created_at` then UUID descending, with `limit+1` lookahead for
`truncated`. Missing projects return 404. The UI shows dataset kind,
version ordinal, persisted lifecycle status and an explicit action to copy
the selected DatasetVersion UUID into the map context form. Versions have to
be ingested/created via their authoritative backend workflow, not through
this read-only listing.

Frontend tests mock XHR events, including 413, abort and unknown total.
Backend endpoint tests cover bounded paging and project isolation. The
full project creation/import/run user journey remains UG-AI-093/094.
