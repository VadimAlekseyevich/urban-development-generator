# S14-T08 — Internal Prometheus operational telemetry

**Task:** UG-AI-102. This is a **read-only operational** adapter, not
project/run performance metrics and not a change to the canonical
`RawMetricId` or `DiagnosticId` registry.

## Configuration and access

The scrape lives at `GET /internal/metrics` (outside
`/api/v1`, excluded from OpenAPI). By default it returns **404**:
configure a distinct random **32+ character** `METRICS_BEARER_TOKEN` in the
API's private environment or secret manager to enable it. Do not reuse a user
session, database password, S3 secret or Redis password. Unauthenticated or
incorrectly authenticated requests return **401** and the
`WWW-Authenticate: Bearer` challenge; the endpoint never returns the
configured token. Traffic should additionally be restricted to a private
monitoring network or reverse proxy. Do not expose the API directly on the
public Internet in production merely because this endpoint has a token.

Example Prometheus 2.x scrape config (secure the token file to the
Prometheus service account, **never commit it**):

```yaml
scrape_configs:
  - job_name: udg-api-ops
    scrape_interval: 30s
    metrics_path: /internal/metrics
    scheme: http  # Use TLS or private authenticated transport in deployment
    static_configs:
      - targets: ['api:8000']
    bearer_token_file: /etc/prometheus/secrets/udg-metrics-token
```

No `prometheus_client` package or push gateway is required; response
`Content-Type: text/plain; version=0.0.4; charset=utf-8` uses the
Prometheus text exposition format. `Cache-Control: no-store` is set.

## Ownership, labels and interpretation

All values are **gauges** and point-in-time measurements. DB records may
be archived or removed, so historical totals must not be represented as
monotonically increasing Prometheus counters.

| Family | Backing source | Labels and meaning |
| --- | --- | --- |
| `udg_jobs` | PostgreSQL `jobs` | `type` × `status`; accepted workload, queued/running/succeeded/failed/cancelled |
| `udg_outbox_messages` | PostgreSQL `job_outbox` | `queue` × `status`; pending vs dispatched |
| `udg_stages` | PostgreSQL `run_stage_results` | 12 canonical `stage` names × six stage `status` values |
| `udg_stage_duration_seconds_sum` | PostgreSQL persisted start/end timestamps | Sum of duration for terminal stages with both timestamps, by canonical stage |
| `udg_stage_duration_samples` | Same | Count of terminal stages included in preceding sum |
| `udg_arq_queue_depth` | Redis ARQ sorted sets (`ZCARD`) | `ingest`, `generation`, `analysis`, `export`, `legacy`; **enqueued**, not actively executing |
| `udg_oldest_queued_job_age_seconds` | PostgreSQL `jobs.created_at` | Oldest DB queued job, or zero |
| `udg_outbox_due` | PostgreSQL `job_outbox` | Pending rows due for enqueue at scrape time |
| `udg_outbox_retries` | PostgreSQL `job_outbox` | Pending rows with nonzero previous delivery attempts |

**Cardinality is closed:** four known job types, five DB logical outbox
queues (including historical `default`), five Redis queues (including
legacy) and the 12 canonical Stage identities; no user-provided job type,
stage name, project/run/artifact UUID, file path, error message, seed,
customer identity or request ID becomes a label. Each scrape emits zero
samples for absent known category/status combinations to keep series stable.

Job counts use authoritative DB state (not Redis snapshots). Redis queue
depth can diverge from pending DB jobs during dispatch, startup, retries
or while an ARQ worker is executing a job. Neither reading a Redis sorted
set nor an aggregate stage duration is a worker heartbeat. Actual worker
up/down status needs service-level scrape availability/health separately.

PostgreSQL summaries use grouped COUNT and server-side elapsed-time sums;
no Job, outbox or RunStageResult object collections are loaded into the
API process. Redis accesses are batched through one pipelined ZCARD set
with one-second connection/read timeouts. If either dependency is
unavailable, the entire scrape fails **503**, instead of falsely reporting
partial values as healthy.

## Reproduction / regression gate

```sh
uv run pytest tests/integration/test_operational_metrics_api.py
uv run ruff check .
uv run mypy backend core worker
```

The integration test migrates a real PostgreSQL schema, persists queued and
failed Jobs, a due retry outbox and a succeeded Stage with timestamps.
It checks exact gauge series, closed labels, zero exposure of IDs/secrets,
404 by default, 401 without the token, 503 when Redis fails, ARQ **ZCARD**
rather than incorrect Redis LLEN and hidden OpenAPI docs.

This is an operational first step, **not** a completed end-to-end production
readiness claim: production DAG wiring, end-to-end load behaviour and
infrastructure-level alerts are tracked separately.
