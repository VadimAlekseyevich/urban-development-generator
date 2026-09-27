"""DB-authoritative publication of artifacts produced by an executing Stage.

Blob promotion is recoverable but cannot participate in the PostgreSQL transaction.
Only the ready -> referenced transition and relational ownership commit atomically.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.generation_run import GenerationRun
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    ArtifactStore,
)


class StageArtifactPublicationError(ValueError):
    """The storage/DB artifact provenance cannot be published consistently."""


@dataclass(frozen=True, slots=True)
class PublishedStageArtifact:
    artifact_id: uuid.UUID
    stage_result_id: uuid.UUID
    stat: ArtifactStat


class SqlAlchemyStageArtifactPublisher:
    """Register temporary, promote the blob, then atomically reference its owner."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._store = store
        self._session_factory = session_factory

    def publish(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        temporary_stat: ArtifactStat,
    ) -> PublishedStageArtifact:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(stage_name, str) or not stage_name:
            raise StageArtifactPublicationError("stage_name must be non-empty")
        if not isinstance(temporary_stat, ArtifactStat):
            raise TypeError("temporary_stat must be ArtifactStat")
        ref = temporary_stat.ref
        if ref.state is not ArtifactState.TEMPORARY:
            raise StageArtifactPublicationError("publication requires a temporary ArtifactStat")
        if not ref.key.startswith(f"runs/{run_id}/stages/{stage_name}/"):
            raise StageArtifactPublicationError("artifact key does not belong to run/stage")

        uri = f"artifact://{ref.key}"
        # stat() may fail after a previous promote and DB failure; the ready blob
        # is then the only durable copy. Both paths must match original metadata.
        storage_stat = self._storage_stat(ref)
        self._require_same_metadata(temporary_stat, storage_stat)

        with self._session_factory() as session:
            with session.begin():
                stage = self._require_active_stage(session, run_id, stage_name)
                row = session.scalar(
                    select(Artifact).where(Artifact.uri == uri).with_for_update()
                )
                if row is None:
                    row = Artifact(
                        uri=uri,
                        checksum=temporary_stat.checksum,
                        size_bytes=temporary_stat.size_bytes,
                        content_type=temporary_stat.content_type,
                        state=ArtifactLifecycleState.TEMPORARY.value,
                    )
                    session.add(row)
                    session.flush()
                else:
                    self._require_record_matches(row, temporary_stat)
                    if row.state == ArtifactLifecycleState.REFERENCED.value:
                        self._require_owner(row, stage.id)
                        return PublishedStageArtifact(
                            artifact_id=row.id,
                            stage_result_id=stage.id,
                            stat=self._store.stat(ref.as_ready()),
                        )
                    if row.state not in {
                        ArtifactLifecycleState.TEMPORARY.value,
                        ArtifactLifecycleState.READY.value,
                    }:
                        raise StageArtifactPublicationError(
                            "artifact is not eligible for publication"
                        )

        # External I/O is intentionally outside the final DB transaction.
        # LocalArtifactStore.promote() can finish an interrupted two-file move.
        ready_stat = self._store.promote(ref)
        self._require_same_metadata(temporary_stat, ready_stat)
        if ready_stat.ref != ref.as_ready():
            raise StageArtifactPublicationError("promote must return the ready artifact ref")

        with self._session_factory() as session:
            with session.begin():
                stage = self._require_active_stage(session, run_id, stage_name)
                row = session.scalar(
                    select(Artifact).where(Artifact.uri == uri).with_for_update()
                )
                if row is None:
                    raise StageArtifactPublicationError("registered artifact disappeared")
                self._require_record_matches(row, ready_stat)
                if row.state == ArtifactLifecycleState.REFERENCED.value:
                    self._require_owner(row, stage.id)
                else:
                    if row.state == ArtifactLifecycleState.TEMPORARY.value:
                        row.transition_to(ArtifactLifecycleState.READY)
                        session.flush()
                    if row.state != ArtifactLifecycleState.READY.value:
                        raise StageArtifactPublicationError(
                            "artifact is not ready to reference"
                        )
                    row.transition_to(
                        ArtifactLifecycleState.REFERENCED,
                        owner_type="run_stage_result",
                        owner_id=stage.id,
                    )
                    stage.artifacts.append(row)
                    session.flush()
                return PublishedStageArtifact(
                    artifact_id=row.id,
                    stage_result_id=stage.id,
                    stat=ready_stat,
                )

    def _storage_stat(self, ref: ArtifactRef) -> ArtifactStat:
        try:
            return self._store.stat(ref)
        except KeyError:
            return self._store.stat(ref.as_ready())

    @staticmethod
    def _require_same_metadata(expected: ArtifactStat, actual: ArtifactStat) -> None:
        if (
            expected.ref.key != actual.ref.key
            or expected.checksum != actual.checksum
            or expected.size_bytes != actual.size_bytes
            or expected.content_type != actual.content_type
        ):
            raise StageArtifactPublicationError("artifact storage metadata does not match")

    @classmethod
    def _require_record_matches(cls, row: Artifact, stat: ArtifactStat) -> None:
        if (
            row.uri != f"artifact://{stat.ref.key}"
            or row.checksum != stat.checksum
            or row.size_bytes != stat.size_bytes
            or row.content_type != stat.content_type
        ):
            raise StageArtifactPublicationError("persisted artifact metadata does not match")

    @staticmethod
    def _require_owner(row: Artifact, stage_result_id: uuid.UUID) -> None:
        if row.owner_type != "run_stage_result" or row.owner_id != stage_result_id:
            raise StageArtifactPublicationError("artifact is already owned by another stage")

    @staticmethod
    def _require_active_stage(
        session: Session,
        run_id: uuid.UUID,
        stage_name: str,
    ) -> RunStageResult:
        run = session.scalar(
            select(GenerationRun)
            .where(GenerationRun.id == run_id)
            .with_for_update()
        )
        if run is None or run.status != "running":
            raise StageArtifactPublicationError("artifact publication requires a running run")
        stage = session.scalar(
            select(RunStageResult)
            .where(
                RunStageResult.run_id == run_id,
                RunStageResult.stage_name == stage_name,
            )
            .with_for_update()
        )
        if stage is None or stage.status != "running":
            raise StageArtifactPublicationError("artifact publication requires a running stage")
        return stage
