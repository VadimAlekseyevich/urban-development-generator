"""Track immutable source-run lineage for exact input reruns."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_exact_rerun_lineage"
down_revision = "0021_scenario_batch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column(
            "rerun_source_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_generation_runs_rerun_source_id",
        "generation_runs",
        "generation_runs",
        ["rerun_source_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_generation_runs_rerun_not_self",
        "generation_runs",
        "rerun_source_id IS NULL OR rerun_source_id <> id",
    )
    op.create_index(
        "ix_generation_runs_rerun_source_id",
        "generation_runs",
        ["rerun_source_id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM generation_runs
                WHERE rerun_source_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot drop exact rerun lineage while rerun records exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_generation_runs_rerun_source_id", table_name="generation_runs")
    op.drop_constraint(
        "ck_generation_runs_rerun_not_self", "generation_runs", type_="check"
    )
    op.drop_constraint(
        "fk_generation_runs_rerun_source_id", "generation_runs", type_="foreignkey"
    )
    op.drop_column("generation_runs", "rerun_source_id")
