"""Run-scoped, project-owned ScenarioBatch and ordered child memberships."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.models.generation_run import GenerationRun
    from backend.app.models.project import Project


class ScenarioBatch(Base):
    """Persisted lifecycle and concurrency bound for 3–10 GenerationRun children."""

    __tablename__ = "scenario_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_scenario_batches_status",
        ),
        CheckConstraint(
            "concurrency_limit BETWEEN 1 AND 10",
            name="ck_scenario_batches_concurrency_limit",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    concurrency_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship("Project")
    members: Mapped[list[ScenarioBatchRun]] = relationship(
        "ScenarioBatchRun", back_populates="batch", order_by="ScenarioBatchRun.position",
        cascade="all, delete-orphan",
    )


class ScenarioBatchRun(Base):
    """One ordered child; a GenerationRun cannot join multiple batches."""

    __tablename__ = "scenario_batch_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_scenario_batch_runs_run"),
        UniqueConstraint("batch_id", "position", name="uq_scenario_batch_runs_position"),
        CheckConstraint("position BETWEEN 0 AND 9", name="ck_scenario_batch_runs_position"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario_batches.id", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    batch: Mapped[ScenarioBatch] = relationship("ScenarioBatch", back_populates="members")
    run: Mapped[GenerationRun] = relationship("GenerationRun")
