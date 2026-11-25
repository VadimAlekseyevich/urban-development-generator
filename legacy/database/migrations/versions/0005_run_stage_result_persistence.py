"""Persist per-stage execution state, provenance hashes, and diagnostics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_run_stage_results"
down_revision = "0004_generation_run_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_stage_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage_name", sa.String(length=64), nullable=False),
        sa.Column("stage_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "progress_percent",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("input_hash", sa.String(length=71), nullable=False),
        sa.Column("config_hash", sa.String(length=71), nullable=False),
        sa.Column(
            "diagnostics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "artifact_refs_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "run_id",
            "stage_name",
            name="uq_run_stage_results_run_stage",
        ),
        sa.CheckConstraint(
            "stage_name ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_run_stage_results_stage_name",
        ),
        sa.CheckConstraint(
            "stage_version ~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'",
            name="ck_run_stage_results_stage_version",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'skipped')",
            name="ck_run_stage_results_status",
        ),
        sa.CheckConstraint(
            "progress_percent BETWEEN 0 AND 100",
            name="ck_run_stage_results_progress_percent",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR progress_percent = 100",
            name="ck_run_stage_results_success_progress",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_run_stage_results_input_hash",
        ),
        sa.CheckConstraint(
            "config_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_run_stage_results_config_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(diagnostics_json) = 'array'",
            name="ck_run_stage_results_diagnostics_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(artifact_refs_json) = 'array'",
            name="ck_run_stage_results_artifact_refs_array",
        ),
    )
    op.create_index(
        "ix_run_stage_results_run_id",
        "run_stage_results",
        ["run_id"],
    )
    op.create_index(
        "ix_run_stage_results_status",
        "run_stage_results",
        ["status"],
    )

    op.execute(
        """
        CREATE FUNCTION guard_completed_run_stage_results()
        RETURNS trigger AS $$
        DECLARE
            target_run_id uuid;
        BEGIN
            target_run_id := CASE
                WHEN TG_OP = 'DELETE' THEN OLD.run_id
                ELSE NEW.run_id
            END;

            IF EXISTS (
                SELECT 1
                FROM generation_runs
                WHERE id = target_run_id
                  AND status = 'succeeded'
            ) THEN
                RAISE EXCEPTION
                    'stage results of a successful generation run are immutable';
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_stage_results_completed_run_guard
        BEFORE INSERT OR UPDATE OR DELETE ON run_stage_results
        FOR EACH ROW
        EXECUTE FUNCTION guard_completed_run_stage_results()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM run_stage_results) THEN
                RAISE EXCEPTION
                    'Cannot downgrade run-stage persistence while stage results exist';
            END IF;
        END
        $$;
        """
    )

    op.execute(
        "DROP TRIGGER trg_run_stage_results_completed_run_guard ON run_stage_results"
    )
    op.execute("DROP FUNCTION guard_completed_run_stage_results()")
    op.drop_index("ix_run_stage_results_status", table_name="run_stage_results")
    op.drop_index("ix_run_stage_results_run_id", table_name="run_stage_results")
    op.drop_table("run_stage_results")
