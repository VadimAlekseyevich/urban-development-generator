"""Bounded DB-authoritative cleanup of abandoned run-stage artifact publication."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.adapters.local_artifact_store import LocalArtifactStore
from backend.app.db.session import SessionLocal
from backend.app.models.artifact import (
    Artifact,
    ArtifactLifecycleState,
    run_stage_result_artifacts,
)
from core.urban_generator.domain import ArtifactRef, ArtifactState

_RUN_STAGE_KEY_RE = re.compile(
    r"^runs/([0-9a-f-]{36})/stages/[a-z][a-z0-9_]{0,63}/.+$"
)


@dataclass(frozen=True, slots=True)
class ArtifactGcResult:
    expired_rows: int
    untracked_keys: int
    examined_candidates: int


class SqlAlchemyArtifactGc:
    """Expire aged unowned run-stage rows and reconcile aged storage-only orphans.

    A row lock serializes deletion against the publisher's final DB transition.
    This cannot make filesystem deletion transactional; a failed delete rolls the
    DB change back, and a later pass repeats idempotently.
    """

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        session_factory: Callable[[], Session] = SessionLocal,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def collect(
        self,
        *,
        min_age: timedelta = timedelta(hours=2),
        max_batch: int = 50,
        max_scan: int = 5000,
    ) -> ArtifactGcResult:
        if min_age < timedelta(hours=1):
            raise ValueError("GC min_age must be at least one hour")
        if not 1 <= max_batch <= 500:
            raise ValueError("GC max_batch must be between 1 and 500")
        if not 1 <= max_scan <= 100_000:
            raise ValueError("GC max_scan must be between 1 and 100000")
        cutoff = self._clock() - min_age
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("GC clock must return timezone-aware datetime")

        # The DB pass does not depend on directory scan ordering; SKIP LOCKED
        # permits parallel collectors without claiming the same live row.
        expired = 0
        with self._session_factory() as session:
            with session.begin():
                rows = session.scalars(
                    select(Artifact)
                    .where(
                        Artifact.state.in_(
                            (
                                ArtifactLifecycleState.TEMPORARY.value,
                                ArtifactLifecycleState.READY.value,
                            )
                        ),
                        Artifact.owner_id.is_(None),
                        Artifact.uri.like("artifact://runs/%"),
                        Artifact.created_at <= cutoff,
                        Artifact.updated_at <= cutoff,
                    )
                    .order_by(Artifact.created_at, Artifact.id)
                    .limit(max_batch)
                    .with_for_update(skip_locked=True)
                ).all()
                for row in rows:
                    ref = self._ref(row.uri)
                    if ref is None or self._is_linked(session, row.id):
                        continue
                    if self._delete_if_stale(ref, cutoff=cutoff):
                        row.transition_to(ArtifactLifecycleState.EXPIRED)
                        expired += 1

        # The storage pass finds both metadata-only and payload-only residue,
        # including a crash after put() but before inserting a DB row.
        untracked = 0
        candidates = self._store.stale_run_refs(
            older_than=cutoff,
            max_scan=max_scan,
            max_results=max_batch,
        )
        for ref in candidates:
            if self._ref(f"artifact://{ref.key}") is None:
                continue
            with self._session_factory() as session:
                with session.begin():
                    row = session.scalar(
                        select(Artifact)
                        .where(Artifact.uri == f"artifact://{ref.key}")
                        .with_for_update(skip_locked=True)
                    )
                    if row is not None:
                        # A referenced, recently touched, expired, or locked row
                        # must never be treated as a storage-only orphan.
                        if (
                            row.state not in {
                                ArtifactLifecycleState.TEMPORARY.value,
                                ArtifactLifecycleState.READY.value,
                            }
                            or row.owner_id is not None
                            or row.created_at > cutoff
                            or row.updated_at > cutoff
                            or self._is_linked(session, row.id)
                        ):
                            continue
                        if self._delete_if_stale(ref, cutoff=cutoff):
                            row.transition_to(ArtifactLifecycleState.EXPIRED)
                            expired += 1
                    elif self._delete_if_stale(ref, cutoff=cutoff):
                        untracked += 1

        return ArtifactGcResult(
            expired_rows=expired,
            untracked_keys=untracked,
            examined_candidates=len(candidates),
        )

    def _delete_if_stale(self, ref: ArtifactRef, *, cutoff: datetime) -> bool:
        if not self._store.is_stale_run_ref(ref, older_than=cutoff):
            return False
        self._store.delete(ref)
        self._store.delete(ArtifactRef(ref.key, state=ArtifactState.READY))
        return True

    @staticmethod
    def _is_linked(session: Session, artifact_id: uuid.UUID) -> bool:
        return session.scalar(
            select(run_stage_result_artifacts.c.artifact_id)
            .where(run_stage_result_artifacts.c.artifact_id == artifact_id)
            .limit(1)
        ) is not None

    @staticmethod
    def _ref(uri: str) -> ArtifactRef | None:
        if not uri.startswith("artifact://"):
            return None
        key = uri.removeprefix("artifact://")
        match = _RUN_STAGE_KEY_RE.fullmatch(key)
        if match is None:
            return None
        try:
            if str(uuid.UUID(match.group(1))) != match.group(1):
                return None
        except ValueError:
            return None
        return ArtifactRef(key)
