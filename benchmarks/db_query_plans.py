"""Reproducible, rollback-only EXPLAIN ANALYZE for actual PostGIS repository SQL.

CI fixture is deliberately synthetic. Plans are evidence, not a surrogate for
25/100 km² production workloads. There are no schema/index changes here.
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, event, text
from sqlalchemy.orm import Session

from backend.app.application.source_layers import SourceLayerBbox, SourceLayerName
from backend.app.db.job_admission import JobCapacityExceeded, enforce_project_job_capacity
from backend.app.db.road_layer_query_repository import SqlAlchemyRoadLayerQueryRepository
from backend.app.db.run_control import SqlAlchemyRunControlService
from backend.app.db.session import engine
from backend.app.db.source_layer_query_repository import SqlAlchemySourceLayerQueryRepository

# Geometry lies on a 50 m grid in EPSG:3857. A narrow WGS84 viewport
# intersects only a small fraction of the populated features.
_NARROW = SourceLayerBbox(0.010, 0.010, 0.012, 0.012)
_WIDE = SourceLayerBbox(-0.01, -0.01, 0.12, 0.06)


def _populate(connection: Connection, *, rows: int) -> dict[str, uuid.UUID]:
    project_id, dataset_id, version_id, other_version_id, run_id = (
        uuid.uuid4() for _ in range(5)
    )
    connection.execute(
        text(
            "INSERT INTO projects (id, name, working_srid, boundary_metadata) "
            "VALUES (:id, 'S14 rollback-only query profile', 3857, '{}'::jsonb)"
        ),
        {"id": project_id},
    )
    connection.execute(
        text("INSERT INTO datasets (id, project_id, kind) "
             "VALUES (:id, :project, 'roads')"),
        {"id": dataset_id, "project": project_id},
    )
    for version, version_uuid in ((1, version_id), (2, other_version_id)):
        connection.execute(
            text(
                "INSERT INTO dataset_versions "
                "(id, dataset_id, version, status, source_metadata) "
                "VALUES (:id, :dataset, :version, 'processing', '{}'::jsonb)"
            ),
            {"id": version_uuid, "dataset": dataset_id, "version": version},
        )
    connection.execute(
        text(
            "INSERT INTO generation_runs "
            "(id, project_id, status, mode, seed, working_srid, config_json, "
            " config_schema_version) VALUES "
            "(:run, :project, 'queued', 'EXPANSION', 1, 3857, '{}'::jsonb, '1')"
        ),
        {"run": run_id, "project": project_id},
    )

    for source_version, prefix in ((version_id, "source-a-"), (other_version_id, "source-b-")):
        connection.execute(
            text(
                "INSERT INTO source_roads "
                "(id, dataset_version_id, source_feature_id, road_class, geometry) "
                "SELECT md5(:prefix || n::text)::uuid, :version, "
                " 'profile-' || n::text, 'local', "
                " ST_Multi(ST_SetSRID(ST_MakeLine("
                " ST_MakePoint(((n - 1) % 200) * 50.0, "
                "              (((n - 1) / 200) % 100) * 50.0), "
                " ST_MakePoint(((n - 1) % 200) * 50.0 + 10, "
                "              (((n - 1) / 200) % 100) * 50.0 + 10)"
                " ), 3857)) "
                "FROM generate_series(1, CAST(:rows AS integer)) AS n"
            ),
            {"prefix": prefix, "version": source_version, "rows": rows},
        )
    connection.execute(
        text(
            "INSERT INTO generated_roads (id, run_id, geometry) "
            "SELECT md5('generated-' || n::text)::uuid, :run, "
            " ST_SetSRID(ST_MakeLine("
            " ST_MakePoint(((n - 1) % 200) * 50.0, "
            "              (((n - 1) / 200) % 100) * 50.0), "
            " ST_MakePoint(((n - 1) % 200) * 50.0 + 10, "
            "              (((n - 1) / 200) % 100) * 50.0 + 10)"
            " ), 3857) "
            "FROM generate_series(1, CAST(:rows AS integer)) AS n"
        ),
        {"run": run_id, "rows": rows},
    )

    history_count = max(50, rows // 10)
    connection.execute(
        text(
            "INSERT INTO generation_runs "
            "(id, project_id, status, mode, seed, working_srid, "
            "config_json, config_schema_version) "
            "SELECT md5('history-' || n::text)::uuid, :project, 'failed', "
            "'EXPANSION', n + 1, 3857, '{}'::jsonb, '1' "
            "FROM generate_series(1, CAST(:count AS integer)) AS n"
        ),
        {"project": project_id, "count": history_count},
    )
    connection.execute(
        text(
            "INSERT INTO jobs (id, project_id, job_type, idempotency_key, status) "
            "SELECT md5('job-' || n::text)::uuid, :project, "
            "CASE WHEN n % 3 = 0 THEN 'ingest_dataset' "
            "WHEN n % 3 = 1 THEN 'generation_run' ELSE 'geojson_export' END, "
            "'profile-' || n::text, "
            "CASE WHEN n % 4 = 0 THEN 'succeeded' ELSE 'queued' END "
            "FROM generate_series(1, CAST(:count AS integer)) AS n"
        ),
        {"project": project_id, "count": max(50, rows // 10)},
    )
    for table in ("source_roads", "generated_roads", "jobs", "generation_runs"):
        connection.exec_driver_sql(f"ANALYZE {table}")
    return {
        "project_id": project_id,
        "version_id": version_id,
        "other_version_id": other_version_id,
        "run_id": run_id,
    }


def _capture_select(
    connection: Connection,
    *,
    table: str,
    operation: Callable[[], object],
) -> tuple[str, Mapping[str, Any] | tuple[Any, ...], object]:
    """Capture the SQL and bind values *actually sent* by the repository."""
    queries: list[tuple[str, Mapping[str, Any] | tuple[Any, ...]]] = []

    def callback(
        _conn: Connection,
        _cursor: object,
        statement: str,
        parameters: Mapping[str, Any] | tuple[Any, ...],
        _context: object,
        executemany: bool,
    ) -> None:
        if (
            not executemany
            and statement.lstrip().upper().startswith("SELECT")
            and f"FROM {table}" in statement
        ):
            queries.append((statement, parameters))

    event.listen(connection, "before_cursor_execute", callback)
    try:
        result = operation()
    finally:
        event.remove(connection, "before_cursor_execute", callback)
    if len(queries) != 1:
        raise RuntimeError(
            f"expected one actual repository SELECT for {table}, captured {len(queries)}"
        )
    return queries[0][0], queries[0][1], result


def _plan_nodes(plan: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        if isinstance(child, dict):
            yield from _plan_nodes(child)


def _summarize(
    *,
    name: str,
    statement: str,
    parameters: Mapping[str, Any] | tuple[Any, ...],
    observed_rows: int | None,
    payload: object,
) -> dict[str, object]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError("PostgreSQL returned an invalid EXPLAIN JSON document")
    result = payload[0]
    if not isinstance(result, dict) or not isinstance(result.get("Plan"), dict):
        raise RuntimeError("PostgreSQL returned an invalid EXPLAIN plan")
    nodes = list(_plan_nodes(result["Plan"]))
    return {
        "name": name,
        "observed_rows": observed_rows,
        "execution_ms": result["Execution Time"],
        "planning_ms": result["Planning Time"],
        "root_node": result["Plan"]["Node Type"],
        "index_names": sorted({
            str(node["Index Name"]) for node in nodes if "Index Name" in node
        }),
        "plan_nodes": [str(node["Node Type"]) for node in nodes],
        "shared_hit_blocks": result["Plan"].get("Shared Hit Blocks", 0),
        "shared_read_blocks": result["Plan"].get("Shared Read Blocks", 0),
        "sql": statement,
        "parameters": {
            str(k): str(v) for k, v in parameters.items()
        } if isinstance(parameters, Mapping) else [str(v) for v in parameters],
        "explain": result,
    }


def profile_queries(*, rows: int) -> dict[str, object]:
    """Profile and roll back *all* synthetic rows, even when a probe raises."""
    if not 100 <= rows <= 40_000:
        raise ValueError("profile row count must be 100..40000")
    plans: list[dict[str, object]] = []
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            ids = _populate(connection, rows=rows)
            session = Session(bind=connection, autoflush=False, expire_on_commit=False)
            try:
                source = SqlAlchemySourceLayerQueryRepository(session)
                roads = SqlAlchemyRoadLayerQueryRepository(session)

                def add(name: str, table: str, callback: Callable[[], object]) -> None:
                    sql, binds, outcome = _capture_select(
                        connection, table=table, operation=callback
                    )
                    connection.exec_driver_sql("SET LOCAL statement_timeout = '15000ms'")
                    payload = connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON, TIMING OFF) " + sql,
                        binds,
                    ).scalar_one()
                    plans.append(_summarize(
                        name=name, statement=sql, parameters=binds,
                        observed_rows=len(outcome) if isinstance(outcome, list) else None,
                        payload=payload,
                    ))

                for name, bounds in (("source_roads_narrow", _NARROW), ("source_roads_wide", _WIDE)):
                    add(name, "source_roads", lambda bounds=bounds: source.list_features(
                        dataset_version_id=ids["version_id"],
                        layer=SourceLayerName.ROADS,
                        working_srid=3857,
                        bbox=bounds,
                        limit=101,
                    ))
                for name, bounds in (("generated_roads_narrow", _NARROW), ("generated_roads_wide", _WIDE)):
                    add(name, "generated_roads", lambda bounds=bounds: roads.list_generated_roads(
                        run_id=ids["run_id"], working_srid=3857,
                        bbox=bounds, limit=101,
                    ))

                def admission() -> None:
                    try:
                        enforce_project_job_capacity(
                            session, project_id=ids["project_id"],
                            job_type="generation_run",
                        )
                    except JobCapacityExceeded:
                        # Admission refusal is expected with the synthetic
                        # saturated project and is itself measured.
                        return

                add("active_job_admission", "jobs", admission)
                run_control = SqlAlchemyRunControlService(
                    session_factory=lambda: Session(
                        bind=connection, autoflush=False, expire_on_commit=False
                    )
                )
                add("latest_project_runs", "generation_runs",
                    lambda: run_control.list(project_id=ids["project_id"], limit=50))
            finally:
                session.close()

            return {
                "profile_schema": "s14-query-profiles-v1",
                "fixture": "synthetic rollback-only",
                "postgres_version": connection.exec_driver_sql(
                    "SHOW server_version"
                ).scalar_one(),
                "working_srid": 3857,
                "rows_per_spatial_owner": rows,
                "source_owner_count": 2,
                "generated_owner_count": 1,
                "historical_runs": max(50, rows // 10) + 1,
                "jobs": max(50, rows // 10),
                "profiles": plans,
            }
        finally:
            transaction.rollback()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = profile_queries(rows=args.rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"S14 PostGIS query profile: {report['postgres_version']} "
          f"rows/owner={report['rows_per_spatial_owner']}")
    for profile in report["profiles"]:
        print(f"  {profile['name']}: execution={profile['execution_ms']:.3f}ms "
              f"root={profile['root_node']} indexes={profile['index_names']} "
              f"observed_rows={profile['observed_rows']}")
    print(f"Full EXPLAIN ANALYZE JSON: {args.output}")


if __name__ == "__main__":
    main()
