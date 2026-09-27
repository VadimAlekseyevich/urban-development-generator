"""PostgreSQL exact-rerun acceptance: validate immutable source, blob and code."""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.adapters import LocalArtifactStore
from backend.app.db.exact_rerun import ExactRerunError, SqlAlchemyExactRerunService
from backend.app.db.session import engine
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from core.urban_generator.domain import ArtifactRef

WORKING_SRID = 32637
COMMIT_SHA = "c" * 40
SessionFactory: Callable[[], Session] = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)


class AvailableCode:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.checked: list[str] = []

    def is_available(self, commit_sha: str) -> bool:
        self.checked.append(commit_sha)
        return self.available and commit_sha == COMMIT_SHA


def _counts() -> tuple[int, int, int]:
    with SessionFactory() as session:
        return (
            session.scalar(select(func.count()).select_from(GenerationRun)) or 0,
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count()).select_from(JobOutbox)) or 0,
        )


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


def _source(
    store: LocalArtifactStore,
    *,
    with_artifact_key: bool = True,
    checksum: bool = True,
    ready: bool = True,
    commit_sha: str | None = COMMIT_SHA,
) -> tuple[uuid.UUID, uuid.UUID, ArtifactRef]:
    payload = b'{"type":"FeatureCollection","features":[]}'
    ref = ArtifactRef(key=f"uploads/{uuid.uuid4().hex}/source.geojson")
    stat = store.promote(store.put(ref, io.BytesIO(payload)).ref)
    with SessionFactory() as session:
        project = Project(
            name="Exact rerun integration",
            working_srid=WORKING_SRID,
            boundary_metadata={},
            boundary=WKTElement(
                "MULTIPOLYGON(((0 0, 10 0, 10 10, 0 10, 0 0)))",
                srid=WORKING_SRID,
            ),
        )
        session.add(project)
        session.flush()
        dataset = Dataset(project_id=project.id, kind="roads")
        session.add(dataset)
        session.flush()
        version = DatasetVersion(
            dataset_id=dataset.id,
            version=1,
            status="ready" if ready else "processing",
            checksum_sha256=stat.checksum.removeprefix("sha256:") if checksum else None,
            source_metadata=(
                {"artifact_key": stat.ref.key, "format": "geojson"}
                if with_artifact_key else {"format": "geojson"}
            ),
        )
        session.add(version)
        session.flush()
        session.add(
            Artifact(
                uri=f"artifact://{stat.ref.key}",
                checksum=stat.checksum,
                size_bytes=stat.size_bytes,
                content_type=stat.content_type,
                state=ArtifactLifecycleState.REFERENCED.value,
                owner_type="dataset_version",
                owner_id=version.id,
            )
        )
        run = GenerationRun(
            project_id=project.id,
            status="queued",
            mode="EXPANSION",
            seed=71,
            working_srid=WORKING_SRID,
            config_json={"nested": {"z": [1, 2], "a": True}, "density": 3},
            config_schema_version="rerun-test-v1",
            commit_sha=commit_sha,
            dataset_versions=[version],
        )
        session.add(run)
        session.flush()
        run.status = "succeeded"
        session.commit()
        return run.id, version.id, stat.ref


def _service(
    store: LocalArtifactStore,
    code: AvailableCode | None = None,
) -> SqlAlchemyExactRerunService:
    return SqlAlchemyExactRerunService(
        session_factory=SessionFactory,
        artifact_store=store,
        code_revisions=code or AvailableCode(),
    )


def test_exact_rerun_clones_inputs_and_creates_new_job_outbox_atomically(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "storage")
    source_id, version_id, _ = _source(store)
    availability = AvailableCode()
    service = _service(store, availability)

    created = service.create(source_run_id=source_id)

    assert availability.checked == [COMMIT_SHA]
    assert created.source_run_id == source_id
    assert created.run_id != source_id
    assert created.dataset_version_ids == (version_id,)
    assert created.config_checksum == "sha256:" + hashlib.sha256(
        b'{"density":3,"nested":{"a":true,"z":[1,2]}}'
    ).hexdigest()

    with SessionFactory() as session:
        source = session.get(GenerationRun, source_id)
        clone = session.get(GenerationRun, created.run_id)
        job = session.get(Job, created.job_id)
        outbox = session.scalar(
            select(JobOutbox).where(JobOutbox.job_id == created.job_id)
        )
        assert source is not None and clone is not None and job is not None
        assert clone.rerun_source_id == source.id
        assert clone.status == job.status == "queued"
        assert (clone.project_id, clone.mode, clone.seed, clone.working_srid) == (
            source.project_id, source.mode, source.seed, source.working_srid
        )
        assert clone.config_json == source.config_json
        assert clone.config_schema_version == source.config_schema_version
        assert clone.commit_sha == source.commit_sha == COMMIT_SHA
        assert tuple(v.id for v in clone.dataset_versions) == (version_id,)
        assert clone.stage_results == []
        assert clone.metrics_json is None and clone.error_json is None
        assert clone.started_at is None and clone.finished_at is None
        assert job.idempotency_key == f"run:{created.run_id}"
        assert job.attempt_count == 0 and job.max_attempts == 3
        assert outbox is not None and outbox.status == "pending"
        assert outbox.queue_name == "generation"
        assert outbox.payload == {
            "task": "run_generation", "run_id": str(created.run_id)
        }
        assert outbox.enqueue_key == f"job:{job.id}"
        assert source.status == "succeeded"
        assert source.rerun_source_id is None
    assert _counts() == (2, 1, 1)

    second = service.create(source_run_id=source_id)
    assert second.run_id not in (source_id, created.run_id)
    assert second.job_id != created.job_id
    assert second.dataset_version_ids == created.dataset_version_ids
    assert second.config_checksum == created.config_checksum
    assert _counts() == (3, 2, 2)


@pytest.mark.parametrize(
    "problem",
    (
        "unavailable_code",
        "missing_commit",
        "missing_version_checksum",
        "unready_version",
        "missing_artifact_key",
        "missing_blob",
        "wrong_db_owner",
        "wrong_db_checksum",
        "wrong_store_checksum",
        "corrupted_payload",
        "changed_boundary_srid",
    ),
)
def test_exact_rerun_rejects_unverified_source_without_partial_creation(
    tmp_path: Path,
    problem: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "storage")
    source_id, version_id, ref = _source(
        store,
        checksum=problem != "missing_version_checksum",
        ready=problem != "unready_version",
        with_artifact_key=problem != "missing_artifact_key",
        commit_sha=None if problem == "missing_commit" else COMMIT_SHA,
    )
    if problem == "missing_blob":
        store.delete(ref)
    if problem in {"wrong_db_owner", "wrong_db_checksum", "changed_boundary_srid"}:
        with SessionFactory() as session:
            if problem == "changed_boundary_srid":
                source = session.get(GenerationRun, source_id)
                assert source is not None
                project = session.get(Project, source.project_id)
                assert project is not None
                project.boundary = WKTElement(
                    "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))",
                    srid=3857,
                )
            else:
                artifact = session.scalar(
                    select(Artifact).where(Artifact.uri == f"artifact://{ref.key}")
                )
                assert artifact is not None
                if problem == "wrong_db_owner":
                    artifact.owner_id = uuid.uuid4()
                else:
                    # Existing referenced artifact metadata is immutable: use SQL
                    # only to prove DB rejects corruption, then simulate an
                    # inconsistent source version checksum instead.
                    version = session.get(DatasetVersion, version_id)
                    assert version is not None
                    with pytest.raises(DBAPIError):
                        with engine.begin() as connection:
                            connection.execute(
                                text("UPDATE dataset_versions SET checksum_sha256 = :checksum "
                                     "WHERE id = :id"),
                                {"checksum": "a" * 64, "id": version_id},
                            )
                    artifact.state = ArtifactLifecycleState.EXPIRED.value
            session.commit()
    code = AvailableCode(available=problem != "unavailable_code")
    if problem == "wrong_store_checksum":
        # LocalArtifactStore metadata is the authoritative stat; corrupting it
        # creates a detectable checksum mismatch without changing the DB row.
        metadata = tmp_path / "storage" / ".metadata" / "ready" / f"{ref.key}.json"
        data = metadata.read_text(encoding="utf-8")
        metadata.write_text(data.replace("sha256:", "sha256:" + "0"), encoding="utf-8")
    if problem == "corrupted_payload":
        payload = tmp_path / "storage" / "ready" / ref.key
        payload.write_bytes(b"x" * payload.stat().st_size)
    before = _counts()

    with pytest.raises(ExactRerunError):
        _service(store, code).create(source_run_id=source_id)

    assert _counts() == before


def test_exact_rerun_rejects_non_successful_or_missing_run(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "storage")
    source_id, _version_id, _ref = _source(store)
    with SessionFactory() as session:
        source = session.get(GenerationRun, source_id)
        assert source is not None
        # Successful rows are DB immutable and cannot be repurposed as failed.
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE generation_runs SET status = 'failed' WHERE id = :id"),
                    {"id": source_id},
                )
    with pytest.raises(ExactRerunError, match="successful source"):
        _service(store).create(source_run_id=uuid.uuid4())


def test_exact_rerun_lineage_is_immutable_after_success(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "storage")
    source_id, _version_id, _ref = _source(store)
    created = _service(store).create(source_run_id=source_id)
    with SessionFactory() as session:
        clone = session.get(GenerationRun, created.run_id)
        assert clone is not None
        clone.status = "succeeded"
        session.commit()
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE generation_runs SET rerun_source_id = NULL WHERE id = :id"),
                {"id": created.run_id},
            )
    with SessionFactory() as session:
        clone = session.get(GenerationRun, created.run_id)
        assert clone is not None and clone.rerun_source_id == source_id
