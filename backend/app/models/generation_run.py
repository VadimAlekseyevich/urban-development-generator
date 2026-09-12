import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

RUN_SUCCESS_STATUS = "succeeded"

IMMUTABLE_SUCCEEDED_RUN_FIELDS = frozenset(
    {
        "id",
        "project_id",
        "status",
        "mode",
        "seed",
        "working_srid",
        "config_json",
        "config_schema_version",
        "commit_sha",
        "metrics_json",
        "error_json",
        "started_at",
        "finished_at",
        "created_at",
    }
)

generation_run_dataset_versions = Table(
    "generation_run_dataset_versions",
    Base.metadata,
    Column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "dataset_version_id",
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class GenerationRunImmutableError(ValueError):
    """Raised when a successful generation run is mutated in place."""


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('EXPANSION', 'FROM_SCRATCH')",
            name="ck_generation_runs_mode",
        ),
        CheckConstraint(
            "working_srid > 0",
            name="ck_generation_runs_working_srid_positive",
        ),
        CheckConstraint(
            "length(config_schema_version) > 0",
            name="ck_generation_runs_config_schema_version_nonempty",
        ),
        CheckConstraint(
            "commit_sha IS NULL OR commit_sha ~ '^[0-9a-f]{40}$'",
            name="ck_generation_runs_commit_sha",
        ),
        CheckConstraint(
            f"status <> '{RUN_SUCCESS_STATUS}' OR commit_sha IS NOT NULL",
            name="ck_generation_runs_success_commit_sha",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", index=True
    )
    mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="EXPANSION"
    )
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    working_srid: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    config_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project = relationship("Project", back_populates="runs")
    dataset_versions: Mapped[list["DatasetVersion"]] = relationship(
        "DatasetVersion",
        secondary=generation_run_dataset_versions,
        order_by="DatasetVersion.dataset_id, DatasetVersion.version",
    )


@event.listens_for(GenerationRun, "before_update")
def prevent_succeeded_generation_run_update(
    _mapper: object,
    _connection: object,
    target: GenerationRun,
) -> None:
    """Reject any scalar mutation after the run has reached success."""

    state = inspect(target)
    status_history = state.attrs.status.history
    previous_status = (
        status_history.deleted[0] if status_history.deleted else target.status
    )
    if previous_status != RUN_SUCCESS_STATUS:
        return

    changed_fields = {
        field_name
        for field_name in IMMUTABLE_SUCCEEDED_RUN_FIELDS
        if state.attrs[field_name].history.has_changes()
    }
    if changed_fields:
        changed = ", ".join(sorted(changed_fields))
        raise GenerationRunImmutableError(
            f"successful generation run is immutable; changed fields: {changed}"
        )


def _ensure_dataset_refs_mutable(target: GenerationRun) -> None:
    if target.status == RUN_SUCCESS_STATUS:
        raise GenerationRunImmutableError(
            "successful generation run dataset refs are immutable"
        )


@event.listens_for(GenerationRun.dataset_versions, "append")
def prevent_succeeded_run_dataset_ref_append(
    target: GenerationRun,
    _value: object,
    _initiator: object,
) -> None:
    _ensure_dataset_refs_mutable(target)


@event.listens_for(GenerationRun.dataset_versions, "remove")
def prevent_succeeded_run_dataset_ref_remove(
    target: GenerationRun,
    _value: object,
    _initiator: object,
) -> None:
    _ensure_dataset_refs_mutable(target)
