"""Add DB-backed job outbox for repeatable queue delivery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_job_outbox"
down_revision = "0007_job_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("queue_name", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("job_id", name="uq_job_outbox_job_id"),
        sa.CheckConstraint(
            "queue_name ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="ck_job_outbox_queue_name",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched')",
            name="ck_job_outbox_status",
        ),
        sa.CheckConstraint(
            "delivery_attempts >= 0",
            name="ck_job_outbox_delivery_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND dispatched_at IS NULL) OR "
            "(status = 'dispatched' AND dispatched_at IS NOT NULL)",
            name="ck_job_outbox_status_timestamp",
        ),
    )
    op.create_index(
        "ix_job_outbox_pending_due",
        "job_outbox",
        ["status", "next_attempt_at", "created_at"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM job_outbox) THEN
                RAISE EXCEPTION 'Cannot downgrade job outbox while outbox rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_job_outbox_pending_due", table_name="job_outbox")
    op.drop_table("job_outbox")
