"""Persist reproducible generation-run inputs and dataset-version references."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_generation_run_persistence"
down_revision = "0003_dataset_versions"
branch_labels = None
depends_on = None

_RUN_SUCCESS_STATUS = "succeeded"


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column("mode", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "generation_runs",
        sa.Column("working_srid", sa.Integer(), nullable=True),
    )
    op.add_column(
        "generation_runs",
        sa.Column("config_schema_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "generation_runs",
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
    )

    op.execute(
        """
        UPDATE generation_runs AS gr
        SET
            mode = 'EXPANSION',
            working_srid = p.working_srid,
            config_schema_version = 'legacy-v0',
            commit_sha = CASE
                WHEN lower(gr.code_version) ~ '^[0-9a-f]{40}$'
                    THEN lower(gr.code_version)
                ELSE NULL
            END
        FROM projects AS p
        WHERE p.id = gr.project_id
        """
    )

    op.alter_column("generation_runs", "mode", nullable=False)
    op.alter_column("generation_runs", "working_srid", nullable=False)
    op.alter_column("generation_runs", "config_schema_version", nullable=False)

    op.create_check_constraint(
        "ck_generation_runs_mode",
        "generation_runs",
        "mode IN ('EXPANSION', 'FROM_SCRATCH')",
    )
    op.create_check_constraint(
        "ck_generation_runs_working_srid_positive",
        "generation_runs",
        "working_srid > 0",
    )
    op.create_check_constraint(
        "ck_generation_runs_config_schema_version_nonempty",
        "generation_runs",
        "length(config_schema_version) > 0",
    )
    op.create_check_constraint(
        "ck_generation_runs_commit_sha",
        "generation_runs",
        "commit_sha IS NULL OR commit_sha ~ '^[0-9a-f]{40}$'",
    )
    op.create_check_constraint(
        "ck_generation_runs_success_commit_sha",
        "generation_runs",
        f"status <> '{_RUN_SUCCESS_STATUS}' OR commit_sha IS NOT NULL",
    )

    op.drop_column("generation_runs", "code_version")

    op.create_table(
        "generation_run_dataset_versions",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_generation_run_dataset_versions_dataset_version_id",
        "generation_run_dataset_versions",
        ["dataset_version_id"],
    )

    op.execute(
        f"""
        CREATE FUNCTION prevent_succeeded_generation_run_update()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status = '{_RUN_SUCCESS_STATUS}'
                AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)
            THEN
                RAISE EXCEPTION
                    'successful generation run is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_generation_runs_immutable_after_success
        BEFORE UPDATE ON generation_runs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_succeeded_generation_run_update()
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION guard_generation_run_dataset_refs()
        RETURNS trigger AS $$
        DECLARE
            old_run_succeeded boolean := false;
            new_run_succeeded boolean := false;
        BEGIN
            IF TG_OP IN ('DELETE', 'UPDATE') THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM generation_runs
                    WHERE id = OLD.run_id
                      AND status = '{_RUN_SUCCESS_STATUS}'
                )
                INTO old_run_succeeded;
            END IF;

            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM generation_runs
                    WHERE id = NEW.run_id
                      AND status = '{_RUN_SUCCESS_STATUS}'
                )
                INTO new_run_succeeded;

                IF NOT EXISTS (
                    SELECT 1
                    FROM generation_runs AS gr
                    JOIN dataset_versions AS dv
                      ON dv.id = NEW.dataset_version_id
                    JOIN datasets AS d
                      ON d.id = dv.dataset_id
                    WHERE gr.id = NEW.run_id
                      AND d.project_id = gr.project_id
                ) THEN
                    RAISE EXCEPTION
                        'dataset version must belong to the generation run project';
                END IF;
            END IF;

            IF old_run_succeeded OR new_run_succeeded THEN
                RAISE EXCEPTION
                    'successful generation run dataset refs are immutable';
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
        CREATE TRIGGER trg_generation_run_dataset_refs_guard
        BEFORE INSERT OR UPDATE OR DELETE
        ON generation_run_dataset_versions
        FOR EACH ROW
        EXECUTE FUNCTION guard_generation_run_dataset_refs()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM generation_runs) THEN
                RAISE EXCEPTION
                    'Cannot downgrade generation-run persistence while runs exist';
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        DROP TRIGGER trg_generation_run_dataset_refs_guard
        ON generation_run_dataset_versions
        """
    )
    op.execute("DROP FUNCTION guard_generation_run_dataset_refs()")
    op.execute(
        "DROP TRIGGER trg_generation_runs_immutable_after_success ON generation_runs"
    )
    op.execute("DROP FUNCTION prevent_succeeded_generation_run_update()")

    op.drop_index(
        "ix_generation_run_dataset_versions_dataset_version_id",
        table_name="generation_run_dataset_versions",
    )
    op.drop_table("generation_run_dataset_versions")

    op.add_column(
        "generation_runs",
        sa.Column("code_version", sa.String(length=128), nullable=True),
    )

    op.drop_constraint(
        "ck_generation_runs_success_commit_sha",
        "generation_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_runs_commit_sha",
        "generation_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_runs_config_schema_version_nonempty",
        "generation_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_runs_working_srid_positive",
        "generation_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_runs_mode",
        "generation_runs",
        type_="check",
    )

    op.drop_column("generation_runs", "commit_sha")
    op.drop_column("generation_runs", "config_schema_version")
    op.drop_column("generation_runs", "working_srid")
    op.drop_column("generation_runs", "mode")
