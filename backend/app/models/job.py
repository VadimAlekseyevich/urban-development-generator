import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from core.urban_generator.domain.errors import UrbanGeneratorError


class Job(Base):
    """Authoritative DB state for one retryable background job."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "job_type",
            "idempotency_key",
            name="uq_jobs_project_type_idempotency",
        ),
        CheckConstraint(
            "job_type ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_jobs_job_type",
        ),
        CheckConstraint(
            "length(btrim(idempotency_key)) > 0",
            name="ck_jobs_idempotency_key_nonempty",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_jobs_attempt_count_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_jobs_max_attempts_positive"),
        CheckConstraint(
            "attempt_count <= max_attempts",
            name="ck_jobs_attempts_within_limit",
        ),
        CheckConstraint(
            "error_class IS NULL OR error_class IN "
            "('domain', 'config', 'data', 'transient', 'permanent', 'cancelled')",
            name="ck_jobs_error_class",
        ),
        CheckConstraint(
            "error_code IS NULL OR length(btrim(error_code)) > 0",
            name="ck_jobs_error_code_nonempty",
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
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued", index=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def record_failure(self, error: UrbanGeneratorError) -> None:
        """Persist stable error taxonomy fields without parsing exception text."""

        self.status = "cancelled" if error.cancelled else "failed"
        self.error_class = error.category.value
        self.error_code = error.code.value
        self.error_json = {
            "message": error.message,
            "details": dict(error.details),
            "retryable": error.retryable,
            "cancelled": error.cancelled,
        }
