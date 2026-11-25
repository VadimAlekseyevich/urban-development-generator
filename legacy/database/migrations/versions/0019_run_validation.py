"""Persist canonical run-level ValidationReport payloads."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_run_validation"
down_revision = "0018_infra_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column(
            "validation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_generation_runs_validation_json_object",
        "generation_runs",
        "validation_json IS NULL OR jsonb_typeof(validation_json) = 'object'",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM generation_runs
                WHERE validation_json IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot drop run validation while validation reports exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(
        "ck_generation_runs_validation_json_object",
        "generation_runs",
        type_="check",
    )
    op.drop_column("generation_runs", "validation_json")
