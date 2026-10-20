# S14-T11 security audit — uploads, paths, SQL, CORS, container identities

**Work item:** UG-AI-105. Scope: repository-level static review and
regression gates. This is **not** a penetration test, production certification,
or claim that unauthenticated endpoints are ready for Internet exposure.
Review the findings before an external deployment.

## Reviewed attack surfaces and evidence

| Surface | Existing controls and reviewed code | Change / unresolved exposure |
| --- | --- | --- |
| Raw uploads | `backend/app/application/uploads.py`: browser filename normalized to a leaf, UUID storage key, actual streaming byte limit in `_SizeLimitedReader`, rollback of partially written artifacts; `tests/unit/test_upload_service.py`, `tests/integration/test_uploads.py` | Existing limits act **after** FastAPI multipart parsing and do not bound temporary parser spooling for direct API clients. Keep nginx `client_max_body_size` and upstream request-size/rate controls. No MIME content signature checking is claimed. |
| ZIP extraction | `backend/app/services/shapefile_zip.py`: normalized relative paths, rejects traversal, symlinks, duplicate/conflicting entries, excessive counts/uncompressed bytes/compression ratios, writes into temporary root; `tests/unit/test_shapefile_zip_extractor.py` | Bounded extraction is covered; do not make this an unconfined generic archive extractor. |
| Artifact storage paths | `core/urban_generator/domain` `ArtifactRef` contract; `backend/app/adapters/local_artifact_store.py` checks resolved root, parent and symlinks. Production profile uses S3, not a host-mounted local path. | Local filesystem path checks are not a substitute for a directory-descriptor/`openat` design in adversarial shared-writable folders; avoid writable untrusted users under the storage root. |
| SQL | Reviewed SQLAlchemy query composition in `backend/app/db/source_layer_query_repository.py`, `vector_layer_query_repository.py`, `mvt_tile_query_repository.py`, `project_repository.py` and dataset-version reader. Layer → table mapping is a fixed allowlist, request values flow through expressions/bound parameters; the fixed `literal_column("mvt_rows")` is not supplied by callers. | No request-controlled SQL identifier or interpolation was identified in this sample. This is **not** a complete repository-wide SQL-injection proof; cover any new raw SQL and dynamic ordering with allowlist reviews and integration tests. |
| CORS | `backend/app/core/config.py`, `backend/app/main.py`: exact http(s) origin validation, reject wildcard/`null`/path/embedded credentials, HTTPS-only remote origins in production; `allow_credentials=False`. | Production defaults to **no** cross-origin browser access. Local developer origin remains permitted for separate Vite frontend. For future cookie auth, implement a deliberate CSRF/origin policy before enabling credentials. |
| Container identities | `compose.production.yml`: API/workers/dispatcher/migrations UID:GID 10001, nginx UID:GID 101; read-only FS, no-new-privileges, all caps dropped and bounded tmpfs; `.github/workflows/ci.yml` asserts identities and production profile. | No daemon escape claim; production external DB/Redis/S3 must use private networks, TLS and least privilege, enforced by deployment environment. |

## Regressions added in this change

- Settings reject wildcard, opaque, malformed, credential-bearing and non-origin URLs.
- Production rejects enabled debug logging and cross-origin HTTP except local loopback.
- Browser CORS preflight grants access only to allowlisted origins and never
  sends `Access-Control-Allow-Credentials`.
- Rendered production Compose CI fails if the frontend silently reverts to root.

## Deployment boundary / residual blockers

**Do not expose this API publicly without an authentication and authorization
boundary.** Project-scoped UUID lookups and CORS do not authenticate users or
isolate tenants. M5 real-data integration and M6 failure/retry acceptance
remain independently required. Enforce request-size ceilings **before**
multipart parsing at the edge and provision rate limits against untrusted
clients. Provision production DB/Redis/S3 with TLS, credentials and network
ACLs; the example file contains placeholders, not a deployable secure network.
Keep `CORS_ORIGINS` empty behind the same-origin production nginx proxy unless
a reviewed HTTPS browser origin is genuinely needed.

## Verification

```sh
uv run pytest tests/unit/test_settings.py tests/unit/test_upload_service.py \
    tests/unit/test_shapefile_zip_extractor.py \
    tests/integration/test_cors_security.py tests/integration/test_uploads.py
uv run ruff check .
uv run mypy backend core worker
docker compose --env-file .env.production -f compose.production.yml config --quiet
```

Required CI (including full tests, frontend and Compose) is the acceptance
gate for this branch; passing only this subset is insufficient. Do not mark
UG-AI-105 complete or deploy until the head commit's checks are green.
