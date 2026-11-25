"""Persist a DB-authoritative, one-way generation cancellation request."""

import sqlalchemy as sa
from alembic import op

revision = "0020_generation_cancel_request"
down_revision = "0019_run_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM jobs WHERE cancel_requested_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot drop generation cancellation request provenance';
            END IF;
        END
        $$;
        """
    )
    op.drop_column("jobs", "cancel_requested_at")
