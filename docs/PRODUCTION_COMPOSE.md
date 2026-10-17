# S14-T10 — Production-like Docker Compose deployment

**Work item:** UG-AI-104. The original `docker-compose.yml` remains the
local development stack; **`compose.production.yml` is independent**, not
an overlay, to avoid inherited development binds, host ports or local services.
This is a **deployment profile**, not proof of release readiness. The
production generation Stage runtime and authentication hardening must be
accepted separately before serving real users.

## Architecture and external dependencies

The profile runs: one-shot Alembic `migrate`, FastAPI `api`, five isolated
ARQ workers (ingest/generation/analysis/export/legacy), outbox `dispatcher`
and static `frontend`. Externalize **PostgreSQL 16 + PostGIS**, **Redis**,
and an **S3-compatible object store**. They are *not* launched here.

- API, migrations, dispatcher and workers share one non-root Python runtime
  image (UID/GID 10001); only fixed application code and frozen production
  dependencies are copied. There are **no repository or storage mounts**.
- The runtime filesystem is read-only with bounded `/tmp` for GDAL/Rasterio
  temporary files. Redis, metadata and source/generated artifacts persist
  outside the containers. Restarting containers cannot destroy durable work.
- Every container drops Linux capabilities and disables privilege escalation.
  Default memory/pid limits and bounded log rotation reduce runaway impact.
  These are reference limits, not calibrated 25/100 km² production SLOs.
- A separate non-root nginx image serves immutable Vite assets and proxies
  only `/api/v1/` to the private API. Vite is **built** with
  `VITE_API_BASE_URL=/api/v1`. Browser API calls are same-origin.
- The only host port defaults to **127.0.0.1:8080** for an external TLS
  reverse proxy; API `:8000`, metrics `/internal/metrics`, PostgreSQL,
  Redis and object-store credentials are not exposed at that boundary.
- The outbox and queue names remain defined by `worker/queues.py`;
  this task does not introduce another background-job or Stage contract.
  The legacy worker remains to drain pre-upgrade ARQ jobs.

## Deployment

```sh
cp .env.production.example .env.production
# Edit .env.production: DATABASE_URL, REDIS_URL, S3_* credentials,
# METRICS_BEARER_TOKEN, CORS_ORIGINS and external endpoints.
chmod 600 .env.production
docker compose --env-file .env.production -f compose.production.yml config --quiet
docker compose --env-file .env.production -f compose.production.yml up -d --build
curl -fsS http://127.0.0.1:8080/api/v1/health/ready
```

The `DATABASE_URL` account must support schema migrations for the one-shot
migrate service. Use a separate migration identity in a future deployment if
your environment requires strict DDL/DML privilege separation; this profile
**does not** claim to implement that separation. Database and S3 buckets must
be provisioned, backed up and secured **before** starting the stack. Use TLS
from all application containers to externally managed endpoints; never expose
plain Redis or PostgreSQL over the public Internet.

For an ingress proxy, keep `PUBLIC_BIND_ADDRESS=127.0.0.1` and terminate
HTTPS at the host/ingress. To expose a different interface, explicitly set
`PUBLIC_BIND_ADDRESS` and take responsibility for TLS, rate limiting and
network ACLs. When increasing `MAX_UPLOAD_SIZE_MB`, also update nginx's
`client_max_body_size` in `docker/nginx.production.conf`. Streaming
`proxy_request_buffering off` prevents buffering multi-GB uploads in the
small nginx tmpfs. Do not check in the populated environment file: it is
gitignored and excluded from Docker build context.

**Secret caveat:** a populated Compose `config` output can contain env
secrets; use `config --quiet` or keep the output private. Docker
`env_file` is not an external secrets manager. Restrict daemon access,
rotate credentials and use an orchestrator with managed secrets for a
hardened deployment.

## Validation, rollback and limitations

CI checks that all services render with real Docker Compose, all backend
services use non-root/read-only/no-bind runtime and external S3, and only the
nginx frontend has a host port. It builds the production API/frontend
images and imports FastAPI using the production Python runtime without
starting any external dependencies. Local dev Compose, unit/integration,
frontend and browser E2E remain unchanged.

```sh
docker compose --env-file .env.production -f compose.production.yml ps
docker compose --env-file .env.production -f compose.production.yml logs --tail=100
docker compose --env-file .env.production -f compose.production.yml down
```

`down` does not delete external Postgres/Redis/S3 data. Revert the deployed
application images/commit only after database migration compatibility is
reviewed; **never** blindly downgrade the shared metadata database.
Check `/api/v1/health/ready`, private metrics and outbox backlog after each
rollout. Existing S14 `JOB_ADMISSION`, `WORKER_QUEUES`,
`GC` and benchmark documentation remain the owners of their policies.

**Non-goals:** no embedded external dependencies, automated DNS/TLS,
cluster autoscaling, Kubernetes manifests, completed auth model, secrets
manager, database backup service, guaranteed Stage execution or load-test SLO.
Later UG-AI-105 must audit upload/path/SQL/CORS/container hardening.
