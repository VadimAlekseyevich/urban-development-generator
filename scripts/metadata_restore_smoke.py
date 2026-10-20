"""S14-T12: seed/verify a DB-only restore using real PostGIS and artifact links.

Run `prepare` on the source database, then use PostgreSQL 16 pg_dump/pg_restore
to restore the archive into an ISOLATED database. Run `verify` on the restore
using the manifest. This program never creates/drops databases or reads S3
objects; an operator must back up blobs independently.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from backend.app.core.config import settings

SCHEMA = "s14-metadata-restore-v1"


def _ids() -> dict[str, str]:
    return {
        name: str(uuid.uuid4())
        for name in (
            "project", "dataset", "version", "road", "run", "stage",
            "upload_artifact", "output_artifact", "job", "outbox",
        )
    }


def _reference_values(ids: dict[str, str]) -> dict[str, str]:
    return {
        "upload_uri": f"artifact://uploads/{ids['project']}/roads.geojson",
        "output_uri": f"artifact://runs/{ids['run']}/roads.geojson",
        "upload_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "commit_sha": "c" * 40,
        "stage_fingerprint": "sha256:" + "d" * 64,
    }


def prepare(*, database_url: str, manifest_path: Path) -> dict[str, object]:
    """Insert a stable cross-table fixture without touching external object bytes."""
    ids = _ids()
    refs = _reference_values(ids)
    manifest: dict[str, object] = {
        "schema": SCHEMA,
        "ids": ids,
        "references": refs,
        "postgres": "16",
        "external_payloads_included": False,
    }
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO projects "
                    "(id, name, working_srid, boundary_metadata, boundary) "
                    "VALUES (:id, 'S14 DB-only restore fixture', 3857, '{}'::jsonb, "
                    "ST_Multi(ST_GeomFromText("
                    "'POLYGON((0 0,100 0,100 100,0 100,0 0))',3857)))"
                ),
                {"id": ids["project"]},
            )
            conn.execute(
                text("INSERT INTO datasets (id, project_id, kind) "
                     "VALUES (:id, :project, 'roads')"),
                {"id": ids["dataset"], "project": ids["project"]},
            )
            conn.execute(
                text(
                    "INSERT INTO dataset_versions "
                    "(id, dataset_id, version, status, checksum_sha256, source_metadata) "
                    "VALUES (:id, :dataset, 1, 'processing', :digest, "
                    "CAST(:metadata AS jsonb))"
                ),
                {
                    "id": ids["version"],
                    "dataset": ids["dataset"],
                    "digest": refs["upload_sha256"],
                    "metadata": json.dumps({
                        "artifact_key": refs["upload_uri"].removeprefix("artifact://"),
                        "format": "geojson",
                    }),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO source_roads "
                    "(id, dataset_version_id, source_feature_id, road_class, geometry) "
                    "VALUES (:id, :version, 'backup-road-001', 'residential', "
                    "ST_GeomFromText('MULTILINESTRING((0 0,50 0))',3857))"
                ),
                {"id": ids["road"], "version": ids["version"]},
            )
            # Readiness is terminal; insert the source layer before sealing it.
            conn.execute(
                text("UPDATE dataset_versions SET status='ready' WHERE id=:id"),
                {"id": ids["version"]},
            )
            conn.execute(
                text(
                    "INSERT INTO generation_runs "
                    "(id, project_id, status, mode, seed, working_srid, config_json, "
                    "config_schema_version, commit_sha, metrics_json) "
                    "VALUES (:id, :project, 'running', 'EXPANSION', 42, 3857, "
                    "'{}'::jsonb, '1', :commit, '{\"roads.length_density_km_per_km2\": 1}'::jsonb)"
                ),
                {
                    "id": ids["run"], "project": ids["project"],
                    "commit": refs["commit_sha"],
                },
            )
            conn.execute(
                text(
                    "INSERT INTO generation_run_dataset_versions "
                    "(run_id, dataset_version_id) VALUES (:run, :version)"
                ),
                {"run": ids["run"], "version": ids["version"]},
            )
            conn.execute(
                text(
                    "INSERT INTO run_stage_results "
                    "(id, run_id, stage_name, stage_version, status, progress_percent, "
                    "input_hash, config_hash, output_fingerprint, "
                    "diagnostics_json, artifact_refs_json, started_at, finished_at) "
                    "VALUES (:id, :run, 'roads', '1.0.0', 'succeeded', 100, "
                    ":hash, :hash, :fingerprint, '[]'::jsonb, '[]'::jsonb, "
                    "now() - interval '2 seconds', now())"
                ),
                {
                    "id": ids["stage"], "run": ids["run"],
                    "hash": "sha256:" + "e" * 64,
                    "fingerprint": refs["stage_fingerprint"],
                },
            )
            for role, owner_type, owner_id, digest in (
                ("upload", "dataset_version", ids["version"], refs["upload_sha256"]),
                ("output", "run_stage_result", ids["stage"], refs["output_sha256"]),
            ):
                conn.execute(
                    text(
                        "INSERT INTO artifacts "
                        "(id, uri, checksum, size_bytes, content_type, state, "
                        "owner_type, owner_id) "
                        "VALUES (:id, :uri, :checksum, 1234, 'application/geo+json', "
                        "'referenced', :owner_type, :owner_id)"
                    ),
                    {
                        "id": ids[f"{role}_artifact"],
                        "uri": refs[f"{role}_uri"],
                        "checksum": "sha256:" + digest,
                        "owner_type": owner_type,
                        "owner_id": owner_id,
                    },
                )
            conn.execute(
                text(
                    "INSERT INTO run_stage_result_artifacts "
                    "(stage_result_id, artifact_id) VALUES (:stage, :artifact)"
                ),
                {"stage": ids["stage"], "artifact": ids["output_artifact"]},
            )
            conn.execute(
                text(
                    "INSERT INTO jobs "
                    "(id, project_id, run_id, job_type, idempotency_key, status) "
                    "VALUES (:id, :project, :run, 'generation_run', :key, 'succeeded')"
                ),
                {
                    "id": ids["job"], "project": ids["project"],
                    "run": ids["run"], "key": f"run:{ids['run']}",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO job_outbox "
                    "(id, job_id, queue_name, payload, status, "
                    "dispatched_at, delivery_attempts) "
                    "VALUES (:id, :job, 'generation', CAST(:payload AS jsonb), "
                    "'dispatched', now(), 1)"
                ),
                {
                    "id": ids["outbox"], "job": ids["job"],
                    "payload": json.dumps({
                        "task": "run_generation", "run_id": ids["run"],
                    }),
                },
            )
            conn.execute(
                text("UPDATE generation_runs SET status='succeeded' WHERE id=:id"),
                {"id": ids["run"]},
            )
    finally:
        engine.dispose()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify(
    *, database_url: str, manifest_path: Path, expected_database: str
) -> dict[str, object]:
    """Fail closed when any ownership, checksum, spatial or migration link drifts."""
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError("unexpected metadata restore manifest schema")
    ids: dict[str, str] = manifest["ids"]
    refs: dict[str, str] = manifest["references"]
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            current_database = conn.execute(text("SELECT current_database()")).scalar_one()
            if current_database != expected_database:
                raise ValueError(
                    "restore verification must target the isolated database: "
                    f"{current_database!r} != {expected_database!r}"
                )
            row = conn.execute(
                text(
                    "SELECT p.working_srid, ST_Area(p.boundary), "
                    "d.kind, v.status, v.checksum_sha256, "
                    "v.source_metadata->>'artifact_key', "
                    "ST_Length(r.geometry), "
                    "gr.status, gr.commit_sha, sr.status, sr.progress_percent, "
                    "sr.output_fingerprint "
                    "FROM projects p "
                    "JOIN datasets d ON d.project_id=p.id "
                    "JOIN dataset_versions v ON v.dataset_id=d.id "
                    "JOIN source_roads r ON r.dataset_version_id=v.id "
                    "JOIN generation_run_dataset_versions rv "
                    "ON rv.dataset_version_id=v.id "
                    "JOIN generation_runs gr ON gr.id=rv.run_id "
                    "JOIN run_stage_results sr ON sr.run_id=gr.id "
                    "WHERE p.id=:project AND d.id=:dataset AND v.id=:version "
                    "AND r.id=:road AND gr.id=:run AND sr.id=:stage"
                ),
                ids,
            ).one()
            expected = (
                3857, 10_000.0, "roads", "ready", refs["upload_sha256"],
                refs["upload_uri"].removeprefix("artifact://"),
                50.0, "succeeded", refs["commit_sha"], "succeeded", 100,
                refs["stage_fingerprint"],
            )
            if tuple(row) != expected:
                raise AssertionError(f"restored PostGIS/run metadata drifted: {row!r}")

            artifacts = conn.execute(
                text(
                    "SELECT id::text, uri, checksum, state, owner_type, owner_id::text "
                    "FROM artifacts WHERE id IN (:upload, :output) ORDER BY uri"
                ),
                {"upload": ids["upload_artifact"], "output": ids["output_artifact"]},
            ).all()
            got = {
                record[0]: tuple(record[1:]) for record in artifacts
            }
            for kind, owner_type, owner_id in (
                ("upload", "dataset_version", ids["version"]),
                ("output", "run_stage_result", ids["stage"]),
            ):
                expected_artifact = (
                    refs[f"{kind}_uri"],
                    "sha256:" + refs[f"{kind}_sha256"],
                    "referenced", owner_type, owner_id,
                )
                if got.get(ids[f"{kind}_artifact"]) != expected_artifact:
                    raise AssertionError(f"restored {kind} artifact metadata drifted")

            assoc = conn.execute(
                text(
                    "SELECT stage_result_id::text, artifact_id::text "
                    "FROM run_stage_result_artifacts "
                    "WHERE stage_result_id=:stage"
                ),
                {"stage": ids["stage"]},
            ).one()
            if tuple(assoc) != (ids["stage"], ids["output_artifact"]):
                raise AssertionError("restored stage-to-artifact link is broken")
            job = conn.execute(
                text(
                    "SELECT j.status, j.run_id::text, ob.queue_name, ob.status, "
                    "ob.payload->>'run_id' "
                    "FROM jobs j JOIN job_outbox ob ON ob.job_id=j.id "
                    "WHERE j.id=:job AND ob.id=:outbox"
                ),
                ids,
            ).one()
            if tuple(job) != (
                "succeeded", ids["run"], "generation", "dispatched", ids["run"]
            ):
                raise AssertionError("restored job/outbox association drifted")
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            postgis = conn.execute(text("SELECT postgis_version()")).scalar_one()
            return {
                "schema": SCHEMA,
                "verified": True,
                "alembic_revision": revision,
                "postgis_version": str(postgis),
                "artifact_references": len(artifacts),
                "external_payloads_restored_by_db_dump": False,
            }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-database")
    args = parser.parse_args()
    if args.action == "prepare":
        report = prepare(database_url=settings.database_url, manifest_path=args.manifest)
        print(json.dumps({"schema": report["schema"], "prepared": True}))
    else:
        if not args.expected_database:
            parser.error("--expected-database is required for verify")
        result = verify(
            database_url=settings.database_url, manifest_path=args.manifest,
            expected_database=args.expected_database,
        )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
