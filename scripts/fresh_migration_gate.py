"""S14-T13 fresh-database migration evidence: schema and immutable guards.

This is deliberately an inspection-only pre/post gate. The CI runner owns the
temporary database's lifecycle. The caller must supply an isolated database
whose name is passed in --expected-database; never fall back to the normal DB.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from backend.app import models  # noqa: F401
from backend.app.core.config import settings
from backend.app.db.base import Base

SCHEMA = "s14-fresh-migration-matrix-v1"
# Tables below must remain present even if SQLAlchemy metadata is refactored.
REQUIRED_TABLES = frozenset((
    "projects", "datasets", "dataset_versions", "artifacts",
    "source_roads", "source_buildings", "source_landuse", "source_water",
    "generation_runs", "run_stage_results", "jobs", "job_outbox",
    "generated_roads", "generated_zones", "generated_blocks",
    "generated_parcels", "generated_buildings", "generated_infrastructure",
    "scenario_batches", "scenario_batch_runs", "geojson_exports",
    "geopackage_exports",
))
REQUIRED_INDEXES = frozenset((
    "ix_source_roads_geometry",
    "ix_generated_roads_geometry",
))


def verify_schema(*, phase: str, expected_database: str) -> dict[str, object]:
    if phase not in {"empty", "migrated"}:
        raise ValueError("phase must be empty or migrated")
    if not expected_database.startswith("urban_generator_release_gate"):
        raise ValueError("release gate must target a disposable database")

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            actual_database = str(conn.scalar(text("SELECT current_database()")))
            if actual_database != expected_database:
                raise RuntimeError(
                    f"refusing release gate against {actual_database!r}; "
                    f"expected {expected_database!r}"
                )
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names(schema="public"))
            actual_models = set(Base.metadata.tables)
            migration_rows: list[str] = []
            if "alembic_version" in table_names:
                migration_rows = list(
                    conn.scalars(text("SELECT version_num FROM alembic_version"))
                )
            expected_heads = ScriptDirectory.from_config(
                Config("alembic.ini")
            ).get_heads()
            if len(expected_heads) != 1:
                raise RuntimeError("release gate requires one linear Alembic head")

            if phase == "empty":
                forbidden = table_names & (actual_models | {"alembic_version"})
                if forbidden:
                    raise RuntimeError(
                        "migration target is not empty: " + ", ".join(sorted(forbidden))
                    )
                if migration_rows:
                    raise RuntimeError("fresh database unexpectedly has Alembic state")
                verified_tables: list[str] = []
                verified_columns: int = 0
                verified_checks: int = 0
                verified_foreign_keys: int = 0
                verified_spatial_indexes: list[str] = []
                source_guard = False
            else:
                missing = (actual_models | REQUIRED_TABLES | {"alembic_version"}) - table_names
                if missing:
                    raise RuntimeError("missing migrated tables: " + ", ".join(sorted(missing)))
                if migration_rows != list(expected_heads):
                    raise RuntimeError(
                        f"Alembic head mismatch: {migration_rows} != {expected_heads}"
                    )
                columns = 0
                check_count = 0
                foreign_key_count = 0
                for name, table in Base.metadata.tables.items():
                    actual_columns = {
                        item["name"] for item in inspector.get_columns(name, schema="public")
                    }
                    missing_columns = {col.name for col in table.columns} - actual_columns
                    if missing_columns:
                        raise RuntimeError(
                            f"{name}: missing model columns {sorted(missing_columns)}"
                        )
                    columns += len(table.columns)
                    check_count += len(inspector.get_check_constraints(name, schema="public"))
                    foreign_key_count += len(inspector.get_foreign_keys(name, schema="public"))

                if check_count < 15 or foreign_key_count < 20:
                    raise RuntimeError(
                        f"schema protection unexpectedly sparse: "
                        f"{check_count} CHECK constraints and {foreign_key_count} FKs"
                    )
                existing_indexes = {
                    name
                    for table in ("source_roads", "generated_roads")
                    for index in inspector.get_indexes(table, schema="public")
                    if (name := index["name"])
                }
                missing_indexes = REQUIRED_INDEXES - existing_indexes
                if missing_indexes:
                    raise RuntimeError(
                        "missing PostGIS spatial indexes: " + ", ".join(sorted(missing_indexes))
                    )
                source_guard_count = int(conn.scalar(text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgrelid = 'public.source_roads'::regclass "
                    "AND NOT tgisinternal"
                )) or 0)
                source_guard = source_guard_count > 0
                if not source_guard:
                    raise RuntimeError("immutable ready source layer has no trigger")

                postgis = str(conn.scalar(text("SELECT PostGIS_Version()")))
                if not postgis:
                    raise RuntimeError("PostGIS extension is not installed")
                sample_geometry = str(conn.scalar(text(
                    "SELECT ST_AsText(ST_GeomFromText('POINT(1 2)', 3857))"
                )))
                if sample_geometry != "POINT(1 2)":
                    raise RuntimeError("PostGIS geometry functions are unavailable")
                verified_tables = sorted(actual_models)
                verified_columns = columns
                verified_checks = check_count
                verified_foreign_keys = foreign_key_count
                verified_spatial_indexes = sorted(REQUIRED_INDEXES)

            return {
                "schema": SCHEMA,
                "database": actual_database,
                "phase": phase,
                "alembic_head": expected_heads[0],
                "actual_revisions": migration_rows,
                "tables_checked": verified_tables,
                "model_columns_checked": verified_columns,
                "check_constraints_found": verified_checks,
                "foreign_keys_found": verified_foreign_keys,
                "spatial_indexes_checked": verified_spatial_indexes,
                "ready_source_immutability_trigger": source_guard,
            }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("empty", "migrated"), required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify_schema(
        phase=args.phase, expected_database=args.expected_database
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Release migration gate [{args.phase}]: "
        f"{report['database']} @ {report['alembic_head']}; "
        f"{len(report['tables_checked'])} model tables, "
        f"{report['model_columns_checked']} columns checked"
    )


if __name__ == "__main__":
    main()
