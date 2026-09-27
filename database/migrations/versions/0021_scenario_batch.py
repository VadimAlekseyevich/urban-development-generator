"""Add project-owned ScenarioBatch with bounded, frozen ordered child runs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_scenario_batch"
down_revision = "0020_generation_cancel_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scenario_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("concurrency_limit", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_scenario_batches_status",
        ),
        sa.CheckConstraint(
            "concurrency_limit BETWEEN 1 AND 10",
            name="ck_scenario_batches_concurrency_limit",
        ),
    )
    op.create_index("ix_scenario_batches_project_id", "scenario_batches", ["project_id"])
    op.create_table(
        "scenario_batch_runs",
        sa.Column(
            "batch_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scenario_batches.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_scenario_batch_runs_run"),
        sa.UniqueConstraint("batch_id", "position", name="uq_scenario_batch_runs_position"),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 9", name="ck_scenario_batch_runs_position",
        ),
    )

    # A draft may be assembled incrementally. Once it is queued, the membership
    # freezes; the 0..9 unique positions enforce the ten-child upper bound.
    op.execute(
        """
        CREATE FUNCTION guard_scenario_batch_membership()
        RETURNS trigger AS $$
        DECLARE
            parent_status text;
            parent_project uuid;
            child_project uuid;
            child_status text;
        BEGIN
            IF TG_OP IN ('DELETE', 'UPDATE') THEN
                SELECT status INTO parent_status
                FROM scenario_batches WHERE id = OLD.batch_id FOR UPDATE;
                IF parent_status IS DISTINCT FROM 'draft' THEN
                    RAISE EXCEPTION 'scenario batch membership is frozen after queueing';
                END IF;
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT status, project_id INTO parent_status, parent_project
                FROM scenario_batches WHERE id = NEW.batch_id FOR UPDATE;
                IF parent_status IS DISTINCT FROM 'draft' THEN
                    RAISE EXCEPTION 'scenario batch membership requires a draft batch';
                END IF;
                SELECT project_id, status INTO child_project, child_status
                FROM generation_runs WHERE id = NEW.run_id FOR SHARE;
                IF child_project IS DISTINCT FROM parent_project THEN
                    RAISE EXCEPTION 'scenario batch child must belong to its project';
                END IF;
                IF child_status IS DISTINCT FROM 'queued' THEN
                    RAISE EXCEPTION 'scenario batch child must be queued when linked';
                END IF;
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
        CREATE TRIGGER trg_scenario_batch_membership_guard
        BEFORE INSERT OR UPDATE OR DELETE ON scenario_batch_runs
        FOR EACH ROW EXECUTE FUNCTION guard_scenario_batch_membership()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_scenario_batch_state()
        RETURNS trigger AS $$
        DECLARE
            child_count integer;
            terminal_count integer;
            failed_count integer;
            cancelled_count integer;
        BEGIN
            IF OLD.status IN ('succeeded', 'failed', 'cancelled')
                AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)
            THEN
                RAISE EXCEPTION 'terminal scenario batch is immutable';
            END IF;
            IF OLD.status <> 'draft' AND (
                NEW.project_id IS DISTINCT FROM OLD.project_id OR
                NEW.concurrency_limit IS DISTINCT FROM OLD.concurrency_limit OR
                NEW.created_at IS DISTINCT FROM OLD.created_at
            ) THEN
                RAISE EXCEPTION 'queued scenario batch ownership and limit are immutable';
            END IF;
            IF NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'draft' AND NEW.status = 'queued' THEN
                SELECT count(*) INTO child_count
                FROM scenario_batch_runs WHERE batch_id = OLD.id;
                IF child_count NOT BETWEEN 3 AND 10
                    OR NEW.concurrency_limit > child_count
                THEN
                    RAISE EXCEPTION 'queued scenario batch requires 3-10 children and valid concurrency';
                END IF;
            ELSIF OLD.status = 'queued'
                AND NEW.status IN ('running', 'succeeded', 'failed', 'cancelled') THEN
                NULL;
            ELSIF OLD.status = 'running'
                AND NEW.status IN ('succeeded', 'failed', 'cancelled') THEN
                NULL;
            ELSE
                RAISE EXCEPTION 'invalid scenario batch lifecycle transition';
            END IF;
            IF NEW.status IN ('succeeded', 'failed', 'cancelled') THEN
                SELECT count(*),
                    count(*) FILTER (WHERE gr.status IN ('succeeded', 'failed', 'cancelled')),
                    count(*) FILTER (WHERE gr.status = 'failed'),
                    count(*) FILTER (WHERE gr.status = 'cancelled')
                INTO child_count, terminal_count, failed_count, cancelled_count
                FROM scenario_batch_runs AS br
                JOIN generation_runs AS gr ON gr.id = br.run_id
                WHERE br.batch_id = OLD.id;
                IF terminal_count <> child_count OR child_count NOT BETWEEN 3 AND 10 THEN
                    RAISE EXCEPTION 'scenario batch terminal state requires all children terminal';
                END IF;
                IF (NEW.status = 'succeeded' AND (failed_count > 0 OR cancelled_count > 0))
                    OR (NEW.status = 'failed' AND failed_count = 0)
                    OR (NEW.status = 'cancelled' AND (failed_count > 0 OR cancelled_count = 0))
                THEN
                    RAISE EXCEPTION 'scenario batch terminal state conflicts with child results';
                END IF;
                IF NEW.finished_at IS NULL THEN
                    RAISE EXCEPTION 'terminal scenario batch requires finished_at';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_scenario_batch_state_guard
        BEFORE UPDATE ON scenario_batches
        FOR EACH ROW EXECUTE FUNCTION guard_scenario_batch_state()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM scenario_batches) THEN
                RAISE EXCEPTION 'Cannot downgrade scenario batch while batches exist';
            END IF;
        END
        $$;
        """
    )
    op.execute("DROP TRIGGER trg_scenario_batch_state_guard ON scenario_batches")
    op.execute("DROP FUNCTION guard_scenario_batch_state()")
    op.execute("DROP TRIGGER trg_scenario_batch_membership_guard ON scenario_batch_runs")
    op.execute("DROP FUNCTION guard_scenario_batch_membership()")
    op.drop_table("scenario_batch_runs")
    op.drop_index("ix_scenario_batches_project_id", table_name="scenario_batches")
    op.drop_table("scenario_batches")
