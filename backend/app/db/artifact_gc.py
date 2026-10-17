"""Bounded DB-authoritative cleanup of abandoned run-stage artifact publication."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.artifact import (
    Artifact,
    ArtifactLifecycleState,
    run_stage_result_artifacts,
)
from backend.app.models.generation_run import GenerationRun
from core.urban_generator.domain import ArtifactRef, ArtifactState


class ArtifactGcStore(Protocol):
    """Infrastructure-only bounded orphan scan shared by storage adapters."""

    def delete(self, ref: ArtifactRef) -> None: ...

    def stale_run_refs(
        self,
        *,
        older_than: datetime,
        max_scan: int,
        max_results: int,
    ) -> tuple[ArtifactRef, ...]: ...

    def has_run_ref(self, ref: ArtifactRef) -> bool: ...

    def is_stale_run_ref(
        self,
        ref: ArtifactRef,
        *,
        older_than: datetime,
    ) -> bool: ...


_RUN_STAGE_KEY_RE = re.compile(
    r"^runs/([0-9a-f-]{36})/stages/[a-z][a-z0-9_]{0,63}/.+$"
)


@dataclass(frozen=True, slots=True)
class ArtifactGcResult:
    expired_rows: int
    untracked_keys: int
    examined_candidates: int
    dry_run: bool = False
    eligible_rows: int = 0
    eligible_untracked_keys: int = 0
    protected_active: int = 0


class SqlAlchemyArtifactGc:
    """Expire aged unowned run-stage rows and reconcile aged storage-only orphans.

    A row lock serializes deletion against the publisher's final DB transition.
    This cannot make filesystem deletion transactional; a failed delete rolls the
    DB change back, and a later pass repeats idempotently.
    """

    def __init__(
        self,
        *,
        store: ArtifactGcStore,
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
        dry_run: bool = False,
    ) -> ArtifactGcResult:
        if type(dry_run) is not bool:
            raise TypeError("GC dry_run must be bool")
        if min_age < timedelta(hours=1):
            raise ValueError("GC min_age must be at least one hour")
        if not 1 <= max_batch <= 500:
            raise ValueError("GC max_batch must be between 1 and 500")
        if not 1 <= max_scan <= 100_000:
            raise ValueError("GC max_scan must be between 1 and 100000")
        cutoff = self._clock() - min_age
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("GC clock must return timezone-aware datetime")

        # DB rows are locked before deciding, as in the existing publisher/
        # collector contract. Dry-run takes the same bounded lock/check path
        # but never transitions state or calls ArtifactStore.delete.
        expired = 0
        eligible_rows = 0
        protected_active = 0
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
                    if self._is_live_run(session, ref):
                        protected_active += 1
                        continue
                    stale_or_missing = (
                        not self._has_storage(ref)
                        or self._store.is_stale_run_ref(ref, older_than=cutoff)
                    )
                    if not stale_or_missing:
                        continue
                    eligible_rows += 1
                    if not dry_run and (
                        not self._has_storage(ref)
                        or self._delete_if_stale(ref, cutoff=cutoff)
                    ):
                        row.transition_to(ArtifactLifecycleState.EXPIRED)
                        expired += 1

        # Storage-only orphans (including crash after put before DB insert)
        # receive no more than the remaining per-pass deletion budget.
        # A dry-run consumes planned slots so its plan respects that same cap.
        untracked = 0
        eligible_untracked = 0
        remaining = max_batch - eligible_rows
        candidates = (
            self._store.stale_run_refs(
                older_than=cutoff,
                max_scan=max_scan,
                max_results=remaining,
            )
            if remaining else ()
        )
        for ref in candidates:
            if self._ref(f"artifact://{ref.key}") is None:
                continue
            with self._session_factory() as session:
                with session.begin():
                    candidate = session.scalar(
                        select(Artifact)
                        .where(Artifact.uri == f"artifact://{ref.key}")
                        .with_for_update()
                    )
                    if self._is_live_run(session, ref):
                        protected_active += 1
                        continue
                    if candidate is not None:
                        if (
                            candidate.state == ArtifactLifecycleState.REFERENCED.value
                            or candidate.owner_id is not None
                            or candidate.created_at > cutoff
                            or candidate.updated_at > cutoff
                            or self._is_linked(session, candidate.id)
                        ):
                            continue
                    if not self._store.is_stale_run_ref(ref, older_than=cutoff):
                        continue
                    if candidate is not None and candidate.state in {
                        ArtifactLifecycleState.TEMPORARY.value,
                        ArtifactLifecycleState.READY.value,
                    }:
                        eligible_rows += 1
                        if not dry_run and self._delete_if_stale(ref, cutoff=cutoff):
                            candidate.transition_to(ArtifactLifecycleState.EXPIRED)
                            expired += 1
                    else:
                        # This also includes leftover bytes for an already
                        # EXPIRED metadata row: count that storage reclamation.
                        eligible_untracked += 1
                        if not dry_run and self._delete_if_stale(ref, cutoff=cutoff):
                            untracked += 1

        return ArtifactGcResult(
            expired_rows=expired,
            untracked_keys=untracked,
            examined_candidates=len(candidates),
            dry_run=dry_run,
            eligible_rows=eligible_rows,
            eligible_untracked_keys=eligible_untracked,
            protected_active=protected_active,
        )

    def _has_storage(self, ref: ArtifactRef) -> bool:
        return self._store.has_run_ref(ref)

    def _delete_if_stale(self, ref: ArtifactRef, *, cutoff: datetime) -> bool:
        if not self._store.is_stale_run_ref(ref, older_than=cutoff):
            return False
        self._store.delete(ref)
        self._store.delete(ArtifactRef(ref.key, state=ArtifactState.READY))
        return True

    @staticmethod
    def _is_live_run(session: Session, ref: ArtifactRef) -> bool:
        """A queued/running run may still be publishing even old stage bytes.

        If a run row was already purged after completion, a two-hour-old
        detached blob may still be cleaned up as an ordinary orphan.
        """
        run_id = uuid.UUID(ref.key.split("/", 2)[1])
        status = session.scalar(
            select(GenerationRun.status).where(GenerationRun.id == run_id)
        )
        return status in {"queued", "running"}

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
