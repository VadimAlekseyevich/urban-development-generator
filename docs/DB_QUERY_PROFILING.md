# S14-T03 — PostgreSQL / PostGIS query-plan evidence

**Task:** UG-AI-097. Query performance is measured with PostgreSQL's
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON, TIMING OFF)` against actual
parameterized SQL statements executed by the application's SQLAlchemy
repositories, not plausible-looking handwritten queries.

## Reproduction

```sh
uv run alembic upgrade head
uv run python -m benchmarks.db_query_plans \
  --rows 10000 --output /tmp/udg-db-query-plans.json
```

The CLI creates one synthetic project in one explicit transaction; it seeds
**two** source-road versions and **one** generated-road run, plus bounded
project run history and mixed-status jobs. Geometries are 50 m-spaced
EPSG:3857 lines. The two source versions contain equivalent geometries to
expose missing source-version owner filters. Only one run is the generated-road
owner. PostGIS statistics are refreshed with `ANALYZE` for the fixture.
The complete transaction is **always rolled back** — no synthetic artifacts,
runs, source features, or jobs survive the measurement.

The profiler wraps actual repository calls with a SQLAlchemy
`before_cursor_execute` listener. It extracts one SELECT, its actual bind
parameters, and then runs EXPLAIN on exactly that statement. It also
records the number of returned view features when applicable. No untrusted
SQL identifiers or user-provided clauses are accepted.

Six scenarios are covered:

| Scenario | Production SQL origin | Data / selectivity |
| --- | --- | --- |
| `source_roads_narrow` | `SqlAlchemySourceLayerQueryRepository.list_features` | Narrow WGS84 viewport, one immutable version |
| `source_roads_wide` | Same | Wider viewport, `LIMIT 101` |
| `generated_roads_narrow` | `SqlAlchemyRoadLayerQueryRepository.list_generated_roads` | Narrow viewport, one run |
| `generated_roads_wide` | Same | Wide viewport, `LIMIT 101` |
| `active_job_admission` | `enforce_project_job_capacity` | Saturated mixed project job statuses |
| `latest_project_runs` | `SqlAlchemyRunControlService.list` | Project history, newest 50 + lookahead |

The full machine-readable result contains PostgreSQL version, fixture size,
observed result sizes, planning/execution milliseconds, top-level plan, every
nested node, index names, shared-hit/read buffer counts, exact captured SQL,
bind values and the full JSON EXPLAIN tree.

## First measured CI baseline — 2026-10-12

Exact feature-head SHA `0da6e2273a0bc279d1091d5fabdecc17909ff77d`,
[workflow run 38241339484](https://github.com/VadimAlekseyevich/urban-development-generator/actions/runs/38241339484):
all four jobs succeeded, including the new measured profile.

PostgreSQL: `16.4 (Debian 16.4-1.pgdg110+2)`; **10,000 rows per spatial owner**,
two source owners, one generated owner. Numbers below are single-run
`EXPLAIN ANALYZE` execution times from this specific runner, **not**
production latency SLOs.

| Production query | Execution (ms) | Features returned | Indexes reported in plan |
| --- | ---: | ---: | --- |
| source roads / narrow | 0.138 | 16 | `ix_source_roads_geometry` (GiST) |
| source roads / wide | 0.216 | 101 | `ix_source_roads_dataset_version_id_id` |
| generated roads / narrow | 0.083 | 16 | `ix_generated_roads_geometry` (GiST) |
| generated roads / wide | 0.186 | 101 | `ix_generated_roads_run_id_id` |
| project active jobs | 0.163 | N/A | No index reported |
| latest project runs | 0.327 | N/A | No index reported |

The existing spatial and owner+ID indexes were selected for the
four viewport cases. No new index is supported by this evidence:
active-job and run-list profiles complete in less than a millisecond on
this small fixture. The lack of an index in their printed plan summaries
**does not** mean those workloads are proven scalable; they must be
reprofiled on real larger histories before any migration.

The full captured SQL, bind parameters, nested plan/buffer numbers, and
planner estimates are archived as the CI artifact `postgis-query-plans`
(retained 14 days) with the workflow above. The benchmark remains
repeatable after that artifact expires.

## CI evidence and interpretation

The Python CI job now runs the ten-thousand-rows-per-owner profile after the
regular unit/integration suite and uploads a 14-day artifact named
`postgis-query-plans`. Job logs contain a compact six-row summary.
`tests/integration/test_db_query_plans.py` independently runs the small
fixture, checks all six real query plans, and proves row counts before/after
are unchanged by the transaction.

- **No indexes are added by this task without measured evidence.**
  PostgreSQL may correctly use bitmap or sequential scans for wide viewports,
  extremely small tables, or high-selectivity owner filters. A missing index
  name in one small-table plan is not itself grounds for a migration.
- The default 10k-row fixture is **synthetic**, on CI's small PostgreSQL
  instance, with warm-cache effects and no background write contention.
  Plan shapes and timing cannot be extrapolated to real 25/100 km² workloads,
  highly clustered OSM geometry, or production hardware.
- Timings are diagnostic observations, **not hard pass/fail budgets**.
  The assertion gate here is safe execution of six real SELECTs plus a
  zero-durable-write fixture. A regression budget needs repeatable reference
  data, runtime variance limits and measurements from S14-T07.
- For a proposed index later: save before/after plans at comparable table
  sizes, examine `Actual Rows` versus `Plan Rows`, shared buffers and
  deterministic EXPLAIN output, then justify the added write/size cost.
  Prefer a narrowly justified Alembic migration, not ad-hoc runtime DDL.
- `EXPLAIN ANALYZE` actually executes each SELECT. Run only on trusted
  schemas (the profiler itself accepts no SQL from users) and never on
  unbounded production queries without a time budget. This harness uses
  `SET LOCAL statement_timeout = '15000ms'` per plan.

**No production-runtime generation success is claimed.** Release issue
#178 (missing real Stage/typed input/config composition) remains independently
open and must be closed before a non-synthetic end-to-end release.
