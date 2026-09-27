"""PostgreSQL/PostGIS and LocalArtifactStore publication/GC integration acceptance."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from backend.app.adapters.local_artifact_store import LocalArtifactStore
from backend.app.db.artifact_gc import SqlAlchemyArtifactGc
from backend.app.db.session import engine
from backend.app.db.stage_artifact_publication import (
    SqlAlchemyStageArtifactPublisher,
    StageArtifactPublicationError,
)
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import ArtifactRef, ArtifactStat
from worker.tasks import gc_orphan_artifacts

WORKING_SRID = 32637
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _running_stage() -> tuple[uuid.UUID, uuid.UUID]:
    with SessionFactory() as session:
        project = Project(
            name="Artifact publication integration",
            working_srid=WORKING_SRID,
            boundary_metadata={},
            boundary=WKTElement(
                "MULTIPOLYGON(((0 0, 10 0, 10 10, 0 10, 0 0)))",
                srid=WORKING_SRID,
            ),
        )
        session.add(project)
        session.flush()
        run = GenerationRun(
            project_id=project.id,
            mode="EXPANSION",
            status="running",
            seed=17,
            working_srid=WORKING_SRID,
            config_json={},
            config_schema_version="fixture-v1",
            commit_sha="a" * 40,
        )
        session.add(run)
        session.flush()
        stage = RunStageResult(
            run_id=run.id,
            stage_name="roads",
            stage_version="fixture-v1",
            status="running",
            progress_percent=0,
            input_hash=f"sha256:{'b' * 64}",
            config_hash=f"sha256:{'c' * 64}",
            output_fingerprint=None,
            diagnostics_json=[],
            artifact_refs_json=[],
        )
        session.add(stage)
        session.commit()
        return run.id, stage.id


def _ref(run_id: uuid.UUID, leaf: str = "result.json") -> ArtifactRef:
    return ArtifactRef(f"runs/{run_id}/stages/roads/{leaf}")


def _published_row(key: str) -> Artifact:
    with SessionFactory() as session:
        row = session.scalar(
            select(Artifact).where(Artifact.uri == f"artifact://{key}")
        )
        assert row is not None
        session.expunge(row)
        return row


def _age_storage(root: Path, ref: ArtifactRef, timestamp: datetime) -> None:
    for state in ("temporary", "ready"):
        for path in (
            root / state / ref.key,
            root / ".metadata" / state / f"{ref.key}.json",
        ):
            if path.exists():
                os.utime(path, (timestamp.timestamp(), timestamp.timestamp()))


def test_publication_atomically_references_stage_and_replays_without_duplicates(
    tmp_path: Path,
) -> None:
    run_id, stage_id = _running_stage()
    store = LocalArtifactStore(tmp_path / "storage")
    ref = _ref(run_id)
    stat = store.put(ref, BytesIO(b'{"roads": 2}'), content_type="application/json")
    publisher = SqlAlchemyStageArtifactPublisher(
        store=store, session_factory=SessionFactory
    )

    published = publisher.publish(
        run_id=run_id, stage_name="roads", temporary_stat=stat
    )

    assert published.stage_result_id == stage_id
    assert published.stat.ref == ref.as_ready()
    assert store.stat(ref.as_ready()) == published.stat
    row = _published_row(ref.key)
    assert row.id == published.artifact_id
    assert row.state == ArtifactLifecycleState.REFERENCED.value
    assert (row.owner_type, row.owner_id) == ("run_stage_result", stage_id)
    assert (row.checksum, row.size_bytes) == (stat.checksum, stat.size_bytes)
    with SessionFactory() as session:
        stage = session.get(RunStageResult, stage_id)
        assert stage is not None
        assert [(artifact.id, artifact.uri) for artifact in stage.artifacts] == [
            (row.id, f"artifact://{ref.key}")
        ]

    replay = publisher.publish(
        run_id=run_id, stage_name="roads", temporary_stat=stat
    )
    assert replay == published
    with SessionFactory() as session:
        assert session.scalar(
            text("SELECT count(*) FROM run_stage_result_artifacts WHERE artifact_id=:id"),
            {"id": published.artifact_id},
        ) == 1


def test_publication_recovers_blob_promoted_before_db_commit(tmp_path: Path) -> None:
    run_id, stage_id = _running_stage()
    store = LocalArtifactStore(tmp_path / "storage")
    ref = _ref(run_id)
    stat = store.put(ref, BytesIO(b"payload"))
    publisher = SqlAlchemyStageArtifactPublisher(
        store=store, session_factory=SessionFactory
    )
    original_promote = store.promote
    calls = 0

    def crash_after_promote(ref_to_promote: ArtifactRef) -> ArtifactStat:
        nonlocal calls
        calls += 1
        ready = original_promote(ref_to_promote)
        if calls == 1:
            raise RuntimeError("simulated crash after storage promotion")
        return ready

    store.promote = crash_after_promote  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="simulated crash"):
        publisher.publish(run_id=run_id, stage_name="roads", temporary_stat=stat)
    row = _published_row(ref.key)
    assert row.state == ArtifactLifecycleState.TEMPORARY.value
    assert row.owner_id is None
    assert store.stat(ref.as_ready()).checksum == stat.checksum

    result = publisher.publish(
        run_id=run_id, stage_name="roads", temporary_stat=stat
    )
    assert result.stage_result_id == stage_id
    assert calls == 2
    assert _published_row(ref.key).state == ArtifactLifecycleState.REFERENCED.value


def test_publication_rejects_wrong_metadata_owner_and_finished_stage(tmp_path: Path) -> None:
    run_id, stage_id = _running_stage()
    store = LocalArtifactStore(tmp_path / "storage")
    ref = _ref(run_id)
    stat = store.put(ref, BytesIO(b"payload"))
    publisher = SqlAlchemyStageArtifactPublisher(
        store=store, session_factory=SessionFactory
    )
    wrong_stat = ArtifactStat(
        ref=ref,
        checksum=f"sha256:{'d' * 64}",
        size_bytes=stat.size_bytes,
    )
    with pytest.raises(StageArtifactPublicationError, match="metadata does not match"):
        publisher.publish(
            run_id=run_id, stage_name="roads", temporary_stat=wrong_stat
        )
    with pytest.raises(StageArtifactPublicationError, match="does not belong"):
        publisher.publish(
            run_id=uuid.uuid4(), stage_name="roads", temporary_stat=stat
        )
    with SessionFactory() as session:
        assert session.scalar(
            select(Artifact).where(Artifact.uri == f"artifact://{ref.key}")
        ) is None
        stage = session.get(RunStageResult, stage_id)
        assert stage is not None
        stage.status = "succeeded"
        stage.progress_percent = 100
        stage.output_fingerprint = f"sha256:{'e' * 64}"
        session.commit()
    with pytest.raises(StageArtifactPublicationError, match="running stage"):
        publisher.publish(run_id=run_id, stage_name="roads", temporary_stat=stat)
    with SessionFactory() as session:
        assert session.scalar(
            select(Artifact).where(Artifact.uri == f"artifact://{ref.key}")
        ) is None


def test_gc_expires_stale_temporary_rows_and_storage_only_orphans(
    tmp_path: Path,
) -> None:
    run_id, stage_id = _running_stage()
    root = tmp_path / "storage"
    store = LocalArtifactStore(root)
    stale = datetime.now(UTC) - timedelta(hours=4)
    row_ref = _ref(run_id, "db-row.json")
    orphan_ref = _ref(run_id, "no-row.json")
    upload_ref = ArtifactRef("uploads/user-source.geojson")
    row_stat = store.put(row_ref, BytesIO(b"db-row"))
    store.put(orphan_ref, BytesIO(b"orphan"))
    store.put(upload_ref, BytesIO(b"important upload"))
    for ref in (row_ref, orphan_ref, upload_ref):
        _age_storage(root, ref, stale)

    with SessionFactory() as session:
        artifact = Artifact(
            uri=f"artifact://{row_ref.key}",
            checksum=row_stat.checksum,
            size_bytes=row_stat.size_bytes,
            content_type=row_stat.content_type,
            state=ArtifactLifecycleState.TEMPORARY.value,
        )
        session.add(artifact)
        session.commit()
        artifact_id = artifact.id
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE artifacts SET created_at=:stale, updated_at=:stale "
                "WHERE id=:artifact_id"
            ),
            {"stale": stale, "artifact_id": artifact_id},
        )

    collector = SqlAlchemyArtifactGc(
        store=store, session_factory=SessionFactory
    )
    first = collector.collect(max_batch=1, max_scan=1000)
    assert first.expired_rows == 1
    assert first.untracked_keys == 0
    assert _published_row(row_ref.key).state == ArtifactLifecycleState.EXPIRED.value
    with pytest.raises(KeyError):
        store.stat(row_ref)
    assert store.stat(orphan_ref).size_bytes == len(b"orphan")

    second = collector.collect(max_batch=1, max_scan=1000)
    assert second.untracked_keys == 1
    assert second.expired_rows == 0
    with pytest.raises(KeyError):
        store.stat(orphan_ref)
    assert store.stat(upload_ref).size_bytes == len(b"important upload")
    assert collector.collect(max_batch=1).untracked_keys == 0
    with SessionFactory() as session:
        stage = session.get(RunStageResult, stage_id)
        assert stage is not None and not stage.artifacts


def test_gc_reconciles_untracked_ready_and_sidecar_only_storage(
    tmp_path: Path,
) -> None:
    run_id, _ = _running_stage()
    root = tmp_path / "storage"
    store = LocalArtifactStore(root)
    old = datetime.now(UTC) - timedelta(hours=4)
    ready_ref = _ref(run_id, "ready-orphan.json")
    partial_ref = _ref(run_id, "sidecar-only.json")
    store.put(ready_ref, BytesIO(b"promoted-before-db-commit"))
    store.promote(ready_ref)
    store.put(partial_ref, BytesIO(b"lost-payload"))
    (root / "temporary" / partial_ref.key).unlink()
    for ref in (ready_ref, partial_ref):
        _age_storage(root, ref, old)

    collected = SqlAlchemyArtifactGc(
        store=store, session_factory=SessionFactory
    ).collect(max_batch=2, max_scan=1000)

    assert collected.untracked_keys == 2
    assert collected.expired_rows == 0
    assert not store.has_run_ref(ready_ref)
    assert not store.has_run_ref(partial_ref)


def test_gc_never_deletes_referenced_or_fresh_blob(tmp_path: Path) -> None:
    run_id, stage_id = _running_stage()
    root = tmp_path / "storage"
    store = LocalArtifactStore(root)
    ref = _ref(run_id, "owned.json")
    stat = store.put(ref, BytesIO(b"owned"))
    result = SqlAlchemyStageArtifactPublisher(
        store=store, session_factory=SessionFactory
    ).publish(run_id=run_id, stage_name="roads", temporary_stat=stat)
    fresh_ref = _ref(run_id, "fresh.json")
    store.put(fresh_ref, BytesIO(b"recent"))
    _age_storage(root, ref, datetime.now(UTC) - timedelta(hours=4))
    collector = SqlAlchemyArtifactGc(store=store, session_factory=SessionFactory)

    assert collector.collect(max_batch=2).untracked_keys == 0
    assert store.stat(ref.as_ready()).checksum == stat.checksum
    assert store.stat(fresh_ref).size_bytes == len(b"recent")
    with SessionFactory() as session:
        stage = session.get(RunStageResult, stage_id)
        assert stage is not None and [x.id for x in stage.artifacts] == [
            result.artifact_id
        ]


def test_gc_worker_entrypoint_and_limits(tmp_path: Path) -> None:
    run_id, _ = _running_stage()
    root = tmp_path / "storage"
    store = LocalArtifactStore(root)
    stale = datetime.now(UTC) - timedelta(hours=4)
    for name in ("one.json", "two.json", "three.json"):
        ref = _ref(run_id, name)
        store.put(ref, BytesIO(name.encode()))
        _age_storage(root, ref, stale)
    collector = SqlAlchemyArtifactGc(
        store=store, session_factory=SessionFactory
    )
    assert len(store.stale_run_refs(
        older_than=datetime.now(UTC) - timedelta(hours=2),
        max_scan=1000,
        max_results=1,
    )) == 1
    with pytest.raises(ValueError, match="at least one hour"):
        collector.collect(min_age=timedelta(seconds=1))
    with pytest.raises(ValueError, match="max_batch"):
        collector.collect(max_batch=0)

    class SingleBatchCollector:
        def collect(self):
            return collector.collect(max_batch=1, max_scan=1000)

    response = asyncio.run(gc_orphan_artifacts({"artifact_gc": SingleBatchCollector()}))
    assert response == {
        "expired_rows": 0,
        "untracked_keys": 1,
        "examined_candidates": 1,
    }
    assert len(store.stale_run_refs(
        older_than=datetime.now(UTC) - timedelta(hours=2),
        max_scan=1000,
        max_results=10,
    )) == 2
