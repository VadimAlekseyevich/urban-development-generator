"""Persist the canonical core StageResult output fingerprint."""

import sqlalchemy as sa
from alembic import op

revision = "0017_stage_output_fingerprint"
down_revision = "0016_building_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_stage_results",
        sa.Column(
            "output_fingerprint",
            sa.String(length=71),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_run_stage_results_output_fingerprint",
        "run_stage_results",
        "output_fingerprint IS NULL OR "
        "output_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM run_stage_results
                WHERE output_fingerprint IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade stage output fingerprint while provenance exists';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(
        "ck_run_stage_results_output_fingerprint",
        "run_stage_results",
        type_="check",
    )
    op.drop_column("run_stage_results", "output_fingerprint")
