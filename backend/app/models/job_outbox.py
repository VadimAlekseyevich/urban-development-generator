import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class JobOutbox(Base):
    """DB-authoritative enqueue state for one background job."""

    __tablename__ = "job_outbox"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_job_outbox_job_id"),
        CheckConstraint(
            "queue_name ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="ck_job_outbox_queue_name",
        ),
        CheckConstraint(
            "status IN ('pending', 'dispatched')",
            name="ck_job_outbox_status",
        ),
        CheckConstraint(
            "delivery_attempts >= 0",
            name="ck_job_outbox_delivery_attempts_nonnegative",
        ),
        CheckConstraint(
            "(status = 'pending' AND dispatched_at IS NULL) OR "
            "(status = 'dispatched' AND dispatched_at IS NOT NULL)",
            name="ck_job_outbox_status_timestamp",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    queue_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", server_default="default"
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def enqueue_key(self) -> str:
        """Stable Redis/ARQ job identity used for repeat delivery."""

        return f"job:{self.job_id}"

    def is_due(self, *, at: datetime) -> bool:
        """Return whether a pending message is eligible for another enqueue attempt."""

        return self.status == "pending" and (
            self.next_attempt_at is None or self.next_attempt_at <= at
        )

    def mark_dispatched(self, *, at: datetime) -> bool:
        """Mark successful enqueue; repeated acknowledgement is a no-op."""

        if self.status == "dispatched":
            return False
        if self.status != "pending":
            raise ValueError(f"unsupported outbox status: {self.status!r}")
        self.delivery_attempts += 1
        self.last_attempt_at = at
        self.next_attempt_at = None
        self.last_error = None
        self.status = "dispatched"
        self.dispatched_at = at
        return True

    def record_delivery_failure(
        self,
        *,
        error: str,
        attempted_at: datetime,
        retry_at: datetime,
    ) -> None:
        """Keep the message pending and schedule a bounded later retry."""

        if self.status != "pending":
            raise ValueError("cannot record delivery failure for a dispatched outbox message")
        error_text = error.strip()
        if not error_text:
            raise ValueError("delivery error must be non-empty")
        if retry_at <= attempted_at:
            raise ValueError("retry_at must be later than attempted_at")
        self.delivery_attempts += 1
        self.last_attempt_at = attempted_at
        self.next_attempt_at = retry_at
        self.last_error = error_text[:2000]
        self.dispatched_at = None
