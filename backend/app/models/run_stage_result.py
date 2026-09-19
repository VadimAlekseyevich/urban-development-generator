import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.artifact import run_stage_result_artifacts

if TYPE_CHECKING:
    from backend.app.models.artifact import Artifact
    from backend.app.models.generation_run import GenerationRun

STAGE_SUCCESS_STATUS = "succeeded"


class RunStageResultImmutableError(ValueError):
    """Raised when artifact provenance of a completed run is changed."""


class RunStageResult(Base):
    """Persistent execution state and provenance for one stage in one run."""

    __tablename__ = "run_stage_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage_name",
            name="uq_run_stage_results_run_stage",
        ),
        CheckConstraint(
            "stage_name ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_run_stage_results_stage_name",
        ),
        CheckConstraint(
            "stage_version ~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'",
            name="ck_run_stage_results_stage_version",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'skipped')",
            name="ck_run_stage_results_status",
        ),
        CheckConstraint(
            "progress_percent BETWEEN 0 AND 100",
            name="ck_run_stage_results_progress_percent",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR progress_percent = 100",
            name="ck_run_stage_results_success_progress",
        ),
        CheckConstraint(
            "input_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_run_stage_results_input_hash",
        ),
        CheckConstraint(
            "config_hash ~ '^sha256:[0-9a-f]{64}
            name="ck_run_stage_results_diagnostics_array",
        ),
        CheckConstraint(
            "jsonb_typeof(artifact_refs_json) = 'array'",
            name="ck_run_stage_results_artifact_refs_array",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    output_fingerprint: Mapped[str | None] = mapped_column(
        String(71),
        nullable=True,
    )
    diagnostics_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    artifact_refs_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    run: Mapped["GenerationRun"] = relationship(
        "GenerationRun",
        back_populates="stage_results",
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact",
        secondary=run_stage_result_artifacts,
        back_populates="stage_results",
        order_by="Artifact.created_at, Artifact.id",
    )


def _ensure_artifact_refs_mutable(target: RunStageResult) -> None:
    run = target.__dict__.get("run")
    if run is not None and run.status == STAGE_SUCCESS_STATUS:
        raise RunStageResultImmutableError(
            "artifact refs of a successful generation run are immutable"
        )


@event.listens_for(RunStageResult.artifacts, "append")
def prevent_completed_run_artifact_ref_append(
    target: RunStageResult,
    _value: object,
    _initiator: object,
) -> None:
    _ensure_artifact_refs_mutable(target)


@event.listens_for(RunStageResult.artifacts, "remove")
def prevent_completed_run_artifact_ref_remove(
    target: RunStageResult,
    _value: object,
    _initiator: object,
) -> None:
    _ensure_artifact_refs_mutable(target)
",
            name="ck_run_stage_results_config_hash",
        ),
        CheckConstraint(
            "output_fingerprint IS NULL OR "
            "output_fingerprint ~ '^sha256:[0-9a-f]{64}
            name="ck_run_stage_results_diagnostics_array",
        ),
        CheckConstraint(
            "jsonb_typeof(artifact_refs_json) = 'array'",
            name="ck_run_stage_results_artifact_refs_array",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    diagnostics_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    artifact_refs_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    run: Mapped["GenerationRun"] = relationship(
        "GenerationRun",
        back_populates="stage_results",
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact",
        secondary=run_stage_result_artifacts,
        back_populates="stage_results",
        order_by="Artifact.created_at, Artifact.id",
    )


def _ensure_artifact_refs_mutable(target: RunStageResult) -> None:
    run = target.__dict__.get("run")
    if run is not None and run.status == STAGE_SUCCESS_STATUS:
        raise RunStageResultImmutableError(
            "artifact refs of a successful generation run are immutable"
        )


@event.listens_for(RunStageResult.artifacts, "append")
def prevent_completed_run_artifact_ref_append(
    target: RunStageResult,
    _value: object,
    _initiator: object,
) -> None:
    _ensure_artifact_refs_mutable(target)


@event.listens_for(RunStageResult.artifacts, "remove")
def prevent_completed_run_artifact_ref_remove(
    target: RunStageResult,
    _value: object,
    _initiator: object,
) -> None:
    _ensure_artifact_refs_mutable(target)
",
            name="ck_run_stage_results_output_fingerprint",
        ),
        CheckConstraint(
            "jsonb_typeof(diagnostics_json) = 'array'",
            name="ck_run_stage_results_diagnostics_array",
        ),
        CheckConstraint(
            "jsonb_typeof(artifact_refs_json) = 'array'",
            name="ck_run_stage_results_artifact_refs_array",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    diagnostics_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    artifact_refs_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    run: Mapped["GenerationRun"] = relationship(
        "GenerationRun",
        back_populates="stage_results",
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact",
        secondary=run_stage_result_artifacts,
        back_populates="stage_results",
        order_by="Artifact.created_at, Artifact.id",
    )


def _ensure_artifact_refs_mutable(target: RunStageResult) -> None:
    run = target.__dict__.get("run")
    if run is not None and run.status == STAGE_SUCCESS_STATUS:
        raise RunStageResultImmutableError(
            "artifact refs of a successful generation run are immutable"
        )


@event.listens_for(RunStageResult.artifacts, "append")
def prevent_completed_run_artifact_ref_append(
    target: RunStageResult,
    _value: object,
    _initiator: object,
) -> None:
    _ensure_artifact_refs_mutable(target)


@event.listens_for(RunStageResult.artifacts, "remove")
def prevent_completed_run_artifact_ref_remove(
    target: RunStageResult,
    _value: object,
    _initiator: object,
) -> None:
    _ensure_artifact_refs_mutable(target)
