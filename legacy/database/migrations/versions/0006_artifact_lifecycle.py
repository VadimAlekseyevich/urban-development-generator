"""Persist artifact metadata, lifecycle state, ownership, and stage references."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_artifact_lifecycle"
down_revision = "0005_run_stage_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("checksum", sa.String(length=71), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="temporary",
        ),
        sa.Column("owner_type", sa.String(length=64), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.UniqueConstraint("uri", name="uq_artifacts_uri"),
        sa.CheckConstraint(
            "length(btrim(uri)) > 0",
            name="ck_artifacts_uri_nonempty",
        ),
        sa.CheckConstraint(
            "checksum ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_artifacts_checksum",
        ),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name="ck_artifacts_size_nonnegative",
        ),
        sa.CheckConstraint(
            "content_type IS NULL OR length(btrim(content_type)) > 0",
            name="ck_artifacts_content_type_nonempty",
        ),
        sa.CheckConstraint(
            "state IN ('temporary', 'ready', 'referenced', 'expired')",
            name="ck_artifacts_state",
        ),
        sa.CheckConstraint(
            "(owner_type IS NULL) = (owner_id IS NULL)",
            name="ck_artifacts_owner_pair",
        ),
        sa.CheckConstraint(
            "owner_type IS NULL OR owner_type ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_artifacts_owner_type",
        ),
        sa.CheckConstraint(
            "state NOT IN ('temporary', 'ready') OR owner_id IS NULL",
            name="ck_artifacts_unowned_pre_reference",
        ),
        sa.CheckConstraint(
            "state <> 'referenced' OR owner_id IS NOT NULL",
            name="ck_artifacts_referenced_owner",
        ),
    )
    op.create_index("ix_artifacts_checksum", "artifacts", ["checksum"])
    op.create_index("ix_artifacts_state", "artifacts", ["state"])
    op.create_index("ix_artifacts_owner_id", "artifacts", ["owner_id"])
    op.create_index(
        "ix_artifacts_owner_type_owner_id",
        "artifacts",
        ["owner_type", "owner_id"],
    )

    op.create_table(
        "run_stage_result_artifacts",
        sa.Column(
            "stage_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_stage_results.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_run_stage_result_artifacts_artifact_id",
        "run_stage_result_artifacts",
        ["artifact_id"],
    )

    op.execute(
        """
        CREATE FUNCTION validate_artifact_lifecycle_update()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.state <> NEW.state
               AND NOT (
                    (OLD.state = 'temporary' AND NEW.state IN ('ready', 'expired'))
                    OR (OLD.state = 'ready' AND NEW.state IN ('referenced', 'expired'))
                    OR (OLD.state = 'referenced' AND NEW.state = 'expired')
               )
            THEN
                RAISE EXCEPTION
                    'invalid artifact lifecycle transition: % -> %',
                    OLD.state,
                    NEW.state;
            END IF;

            IF OLD.state IN ('ready', 'referenced', 'expired')
               AND (
                    NEW.uri IS DISTINCT FROM OLD.uri
                    OR NEW.checksum IS DISTINCT FROM OLD.checksum
                    OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
                    OR NEW.content_type IS DISTINCT FROM OLD.content_type
               )
            THEN
                RAISE EXCEPTION
                    'artifact content metadata is immutable after ready';
            END IF;

            IF (
                NEW.owner_type IS DISTINCT FROM OLD.owner_type
                OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
            )
               AND NOT (
                    OLD.state = 'ready'
                    AND NEW.state = 'referenced'
               )
            THEN
                RAISE EXCEPTION
                    'artifact owner can only be assigned during ready -> referenced';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_artifacts_lifecycle_guard
        BEFORE UPDATE ON artifacts
        FOR EACH ROW
        EXECUTE FUNCTION validate_artifact_lifecycle_update()
        """
    )

    op.execute(
        """
        CREATE FUNCTION guard_completed_run_stage_artifact_refs()
        RETURNS trigger AS $$
        DECLARE
            target_stage_result_id uuid;
        BEGIN
            target_stage_result_id := CASE
                WHEN TG_OP = 'DELETE' THEN OLD.stage_result_id
                ELSE NEW.stage_result_id
            END;

            IF EXISTS (
                SELECT 1
                FROM run_stage_results AS stage_result
                JOIN generation_runs AS generation_run
                  ON generation_run.id = stage_result.run_id
                WHERE stage_result.id = target_stage_result_id
                  AND generation_run.status = 'succeeded'
            ) THEN
                RAISE EXCEPTION
                    'artifact refs of a successful generation run are immutable';
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
        CREATE TRIGGER trg_run_stage_result_artifacts_completed_run_guard
        BEFORE INSERT OR UPDATE OR DELETE ON run_stage_result_artifacts
        FOR EACH ROW
        EXECUTE FUNCTION guard_completed_run_stage_artifact_refs()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM artifacts) THEN
                RAISE EXCEPTION
                    'Cannot downgrade artifact persistence while artifacts exist';
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        DROP TRIGGER
            trg_run_stage_result_artifacts_completed_run_guard
        ON run_stage_result_artifacts
        """
    )
    op.execute("DROP FUNCTION guard_completed_run_stage_artifact_refs()")
    op.execute("DROP TRIGGER trg_artifacts_lifecycle_guard ON artifacts")
    op.execute("DROP FUNCTION validate_artifact_lifecycle_update()")
    op.drop_index(
        "ix_run_stage_result_artifacts_artifact_id",
        table_name="run_stage_result_artifacts",
    )
    op.drop_table("run_stage_result_artifacts")
    op.drop_index("ix_artifacts_owner_type_owner_id", table_name="artifacts")
    op.drop_index("ix_artifacts_owner_id", table_name="artifacts")
    op.drop_index("ix_artifacts_state", table_name="artifacts")
    op.drop_index("ix_artifacts_checksum", table_name="artifacts")
    op.drop_table("artifacts")
