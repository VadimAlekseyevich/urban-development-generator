import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    UniqueConstraint,
    event,
    func,
    inspect,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.models.run_stage_result import RunStageResult


class ArtifactLifecycleState(StrEnum):
    TEMPORARY = "temporary"
    READY = "ready"
    REFERENCED = "referenced"
    EXPIRED = "expired"


class ArtifactLifecycleError(ValueError):
    """Raised when artifact metadata or lifecycle transitions are invalid."""


run_stage_result_artifacts = Table(
    "run_stage_result_artifacts",
    Base.metadata,
    Column(
        "stage_result_id",
        UUID(as_uuid=True),
        ForeignKey("run_stage_results.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "artifact_id",
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)

_IMMUTABLE_CONTENT_FIELDS = frozenset({"uri", "checksum", "size_bytes", "content_type"})
_ALLOWED_TRANSITIONS = {
    ArtifactLifecycleState.TEMPORARY: frozenset(
        {ArtifactLifecycleState.READY, ArtifactLifecycleState.EXPIRED}
    ),
    ArtifactLifecycleState.READY: frozenset(
        {ArtifactLifecycleState.REFERENCED, ArtifactLifecycleState.EXPIRED}
    ),
    ArtifactLifecycleState.REFERENCED: frozenset({ArtifactLifecycleState.EXPIRED}),
    ArtifactLifecycleState.EXPIRED: frozenset(),
}


class Artifact(Base):
    """Persistent metadata and lifecycle for one blob managed by ArtifactStore."""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("uri", name="uq_artifacts_uri"),
        CheckConstraint(
            "length(btrim(uri)) > 0",
            name="ck_artifacts_uri_nonempty",
        ),
        CheckConstraint(
            "checksum ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_artifacts_checksum",
        ),
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_artifacts_size_nonnegative",
        ),
        CheckConstraint(
            "content_type IS NULL OR length(btrim(content_type)) > 0",
            name="ck_artifacts_content_type_nonempty",
        ),
        CheckConstraint(
            "state IN ('temporary', 'ready', 'referenced', 'expired')",
            name="ck_artifacts_state",
        ),
        CheckConstraint(
            "(owner_type IS NULL) = (owner_id IS NULL)",
            name="ck_artifacts_owner_pair",
        ),
        CheckConstraint(
            "owner_type IS NULL OR owner_type ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_artifacts_owner_type",
        ),
        CheckConstraint(
            "state NOT IN ('temporary', 'ready') OR owner_id IS NULL",
            name="ck_artifacts_unowned_pre_reference",
        ),
        CheckConstraint(
            "state <> 'referenced' OR owner_id IS NOT NULL",
            name="ck_artifacts_referenced_owner",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    checksum: Mapped[str] = mapped_column(String(71), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ArtifactLifecycleState.TEMPORARY.value,
        server_default=ArtifactLifecycleState.TEMPORARY.value,
        index=True,
    )
    owner_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    stage_results: Mapped[list["RunStageResult"]] = relationship(
        "RunStageResult",
        secondary=run_stage_result_artifacts,
        back_populates="artifacts",
        order_by="RunStageResult.stage_name",
    )

    def transition_to(
        self,
        target: ArtifactLifecycleState,
        *,
        owner_type: str | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> None:
        """Apply one valid lifecycle transition without changing blob identity."""

        if not isinstance(target, ArtifactLifecycleState):
            raise ArtifactLifecycleError("target state must be an ArtifactLifecycleState")

        current = ArtifactLifecycleState(
            self.state or ArtifactLifecycleState.TEMPORARY.value
        )
        _validate_transition(current, target)

        if target is ArtifactLifecycleState.REFERENCED:
            _validate_owner(owner_type, owner_id)
            self.owner_type = owner_type
            self.owner_id = owner_id
        elif owner_type is not None or owner_id is not None:
            raise ArtifactLifecycleError(
                "owner can only be assigned when transitioning to referenced"
            )

        self.state = target.value


def _validate_transition(
    current: ArtifactLifecycleState,
    target: ArtifactLifecycleState,
) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ArtifactLifecycleError(
            f"invalid artifact lifecycle transition: {current.value} -> {target.value}"
        )


def _validate_owner(owner_type: str | None, owner_id: uuid.UUID | None) -> None:
    if owner_type is None or owner_id is None:
        raise ArtifactLifecycleError("referenced artifacts require owner_type and owner_id")
    if not isinstance(owner_type, str) or not owner_type.strip():
        raise ArtifactLifecycleError("owner_type must be a non-empty string")
    if not isinstance(owner_id, uuid.UUID):
        raise ArtifactLifecycleError("owner_id must be a UUID")


@event.listens_for(Artifact, "before_update")
def prevent_invalid_artifact_update(
    _mapper: object,
    _connection: object,
    target: Artifact,
) -> None:
    """Mirror lifecycle immutability before changes reach PostgreSQL."""

    state = inspect(target)
    state_history = state.attrs.state.history
    previous_raw = state_history.deleted[0] if state_history.deleted else target.state
    current_raw = target.state

    previous = ArtifactLifecycleState(previous_raw)
    current = ArtifactLifecycleState(current_raw)
    if previous is not current:
        _validate_transition(previous, current)

    changed_content = {
        field_name
        for field_name in _IMMUTABLE_CONTENT_FIELDS
        if state.attrs[field_name].history.has_changes()
    }
    if previous is not ArtifactLifecycleState.TEMPORARY and changed_content:
        changed = ", ".join(sorted(changed_content))
        raise ArtifactLifecycleError(
            f"artifact content metadata is immutable after ready; changed fields: {changed}"
        )

    owner_changed = (
        state.attrs.owner_type.history.has_changes()
        or state.attrs.owner_id.history.has_changes()
    )
    if owner_changed and not (
        previous is ArtifactLifecycleState.READY
        and current is ArtifactLifecycleState.REFERENCED
    ):
        raise ArtifactLifecycleError(
            "artifact owner can only be assigned during ready -> referenced"
        )

    if current is ArtifactLifecycleState.REFERENCED:
        _validate_owner(target.owner_type, target.owner_id)
