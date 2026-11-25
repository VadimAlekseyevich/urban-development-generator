"""Exact rerun of immutable successful run inputs with source-blob verification.

The original successful run is never altered. A fresh run, job and pending
outbox message are created atomically through the existing generation path.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.scenario_matrix import (
    ScenarioConfigVariant,
    ScenarioMatrixError,
)
from backend.app.db.session import SessionLocal
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import (
    GenerationRun,
    generation_run_dataset_versions,
)
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from core.urban_generator.domain import (
    ArtifactContractError,
    ArtifactRef,
    ArtifactState,
    ArtifactStore,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_READ_CHUNK_BYTES = 1024 * 1024


class ExactRerunError(ValueError):
    """Requested source provenance is missing, unavailable or unverifiable."""


class CodeRevisionAvailability(Protocol):
    """Check that the exact recorded commit can actually run in this deployment.

    Implementations must verify the executable revision/image, not merely accept
    a syntactically valid SHA. No implicit fallback to today's code is permitted.
    """

    def is_available(self, commit_sha: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ExactRerunCreation:
    source_run_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID
    config_checksum: str
    dataset_version_ids: tuple[uuid.UUID, ...]


class SqlAlchemyExactRerunService:
    """Clone verified persisted input provenance without reusing generated outputs."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        code_revisions: CodeRevisionAvailability,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._store = artifact_store
        self._code_revisions = code_revisions
        self._session_factory = session_factory

    def create(self, *, source_run_id: uuid.UUID) -> ExactRerunCreation:
        if not isinstance(source_run_id, uuid.UUID):
            raise TypeError("source_run_id must be UUID")

        with self._session_factory() as session:
            with session.begin():
                source = session.scalar(
                    select(GenerationRun)
                    .where(GenerationRun.id == source_run_id)
                    .with_for_update(read=True)
                )
                if source is None or source.status != "succeeded":
                    raise ExactRerunError(
                        "exact rerun requires an existing successful source run"
                    )
                project = session.scalar(
                    select(Project)
                    .where(Project.id == source.project_id)
                    .with_for_update(read=True)
                )
                if project is None or project.working_srid != source.working_srid:
                    raise ExactRerunError(
                        "source project and run metric working SRID are inconsistent"
                    )
                if (
                    project.boundary is None
                    or project.boundary.srid != source.working_srid
                ):
                    raise ExactRerunError(
                        "source project boundary is unavailable in its metric working SRID"
                    )
                if (
                    source.commit_sha is None
                    or _COMMIT_RE.fullmatch(source.commit_sha) is None
                    or self._code_revisions.is_available(source.commit_sha) is not True
                ):
                    raise ExactRerunError(
                        "source code commit is missing or not executable in this deployment"
                    )
                try:
                    config = ScenarioConfigVariant.from_config(
                        name="exact_rerun", config_json=source.config_json
                    )
                except ScenarioMatrixError as exc:
                    raise ExactRerunError(
                        "source config is not a canonical finite JSON object"
                    ) from exc
                if (
                    not isinstance(source.config_schema_version, str)
                    or not source.config_schema_version.strip()
                ):
                    raise ExactRerunError("source config schema version is missing")
                canonical_config = config.canonical_config_json
                config_checksum = "sha256:" + hashlib.sha256(
                    canonical_config.encode("utf-8")
                ).hexdigest()

                source_rows = session.execute(
                    select(DatasetVersion, Dataset)
                    .join(
                        generation_run_dataset_versions,
                        generation_run_dataset_versions.c.dataset_version_id
                        == DatasetVersion.id,
                    )
                    .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                    .where(generation_run_dataset_versions.c.run_id == source.id)
                    .order_by(
                        Dataset.kind,
                        DatasetVersion.dataset_id,
                        DatasetVersion.version,
                        DatasetVersion.id,
                    )
                    .with_for_update(of=DatasetVersion, read=True)
                ).all()
                versions: list[DatasetVersion] = []
                for version, dataset in source_rows:
                    if dataset.project_id != source.project_id:
                        raise ExactRerunError(
                            "source run contains a foreign-project dataset version"
                        )
                    if version.status != "ready":
                        raise ExactRerunError(
                            f"source dataset version is not ready: {version.id}"
                        )
                    self._verify_version_artifact(session, version)
                    versions.append(version)

                run_id = uuid.uuid4()
                job_id = uuid.uuid4()
                session.add(
                    GenerationRun(
                        id=run_id,
                        rerun_source_id=source.id,
                        project_id=source.project_id,
                        status="queued",
                        mode=source.mode,
                        seed=source.seed,
                        working_srid=source.working_srid,
                        config_json=config.config_json,
                        config_schema_version=source.config_schema_version,
                        commit_sha=source.commit_sha,
                        dataset_versions=versions,
                    )
                )
                session.flush()  # new run and pinned dataset refs
                session.add(
                    Job(
                        id=job_id,
                        project_id=source.project_id,
                        run_id=run_id,
                        job_type="generation_run",
                        idempotency_key=f"run:{run_id}",
                        status="queued",
                        attempt_count=0,
                        max_attempts=3,
                    )
                )
                session.flush()
                session.add(
                    JobOutbox(
                        job_id=job_id,
                        queue_name="generation",
                        payload={"task": "run_generation", "run_id": str(run_id)},
                        status="pending",
                    )
                )
                session.flush()
                return ExactRerunCreation(
                    source_run_id=source.id,
                    run_id=run_id,
                    job_id=job_id,
                    config_checksum=config_checksum,
                    dataset_version_ids=tuple(version.id for version in versions),
                )

    def _verify_version_artifact(
        self, session: Session, version: DatasetVersion
    ) -> None:
        checksum = version.checksum_sha256
        if checksum is None or _SHA256_RE.fullmatch(checksum) is None:
            raise ExactRerunError(
                f"source dataset version has no verifiable SHA-256: {version.id}"
            )
        key = version.source_metadata.get("artifact_key")
        if not isinstance(key, str):
            raise ExactRerunError(
                f"source dataset version has no artifact_key: {version.id}"
            )
        try:
            ref = ArtifactRef(key=key, state=ArtifactState.READY)
        except ArtifactContractError as exc:
            raise ExactRerunError(
                f"source dataset version has an invalid artifact key: {version.id}"
            ) from exc
        artifact = session.scalar(
            select(Artifact)
            .where(Artifact.uri == f"artifact://{ref.key}")
            .with_for_update(read=True)
        )
        expected_checksum = f"sha256:{checksum}"
        if (
            artifact is None
            or artifact.state != ArtifactLifecycleState.REFERENCED.value
            or artifact.owner_type != "dataset_version"
            or artifact.owner_id != version.id
            or artifact.checksum != expected_checksum
        ):
            raise ExactRerunError(
                f"source dataset artifact ownership/checksum is invalid: {version.id}"
            )
        try:
            stat = self._store.stat(ref)
            if (
                stat.ref != ref
                or stat.checksum != expected_checksum
                or stat.checksum != artifact.checksum
                or stat.size_bytes != artifact.size_bytes
                or stat.content_type != artifact.content_type
            ):
                raise ExactRerunError(
                    f"source artifact metadata/hash mismatch: {version.id}"
                )
            observed = 0
            digest = hashlib.sha256()
            with self._store.open(ref) as stream:
                while True:
                    chunk = stream.read(_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    observed += len(chunk)
                    if observed > artifact.size_bytes:
                        raise ExactRerunError(
                            f"source artifact payload exceeds recorded size: {version.id}"
                        )
                    digest.update(chunk)
        except (KeyError, OSError, ArtifactContractError) as exc:
            raise ExactRerunError(
                f"source artifact is unavailable or unreadable: {version.id}"
            ) from exc
        if observed != artifact.size_bytes or digest.hexdigest() != checksum:
            raise ExactRerunError(
                f"source artifact payload hash/size mismatch: {version.id}"
            )
