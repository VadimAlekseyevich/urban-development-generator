"""Enforce terminal published tile inputs at the database boundary (S13-T04).

A ready source dataset must never be reopened in place: its normalized
features were already protected by row triggers, but previously the status
could be changed back to processing. Published run inputs likewise must not
silently change their project working CRS or source version project owner.
"""

from alembic import op

revision = "0023_published_tile_inputs"
down_revision = "0022_exact_rerun_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_published_dataset_version_status()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'ready' AND NEW.status IS DISTINCT FROM 'ready' THEN
                RAISE EXCEPTION
                    'ready dataset version status is terminal; create a new version';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dataset_versions_terminal_ready
        BEFORE UPDATE OF status ON dataset_versions
        FOR EACH ROW
        EXECUTE FUNCTION guard_published_dataset_version_status()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_published_project_working_srid()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.working_srid IS DISTINCT FROM OLD.working_srid
               AND (
                   EXISTS (
                       SELECT 1 FROM datasets AS d
                       JOIN dataset_versions AS dv ON dv.dataset_id = d.id
                       WHERE d.project_id = OLD.id AND dv.status = 'ready'
                   )
                   OR EXISTS (
                       SELECT 1 FROM generation_runs AS r
                       WHERE r.project_id = OLD.id AND r.status = 'succeeded'
                   )
               )
            THEN
                RAISE EXCEPTION
                    'project working_srid is immutable after source/run publication';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_projects_published_working_srid
        BEFORE UPDATE OF working_srid ON projects
        FOR EACH ROW
        EXECUTE FUNCTION guard_published_project_working_srid()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_published_dataset_project()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.project_id IS DISTINCT FROM OLD.project_id
               AND EXISTS (
                   SELECT 1
                   FROM dataset_versions AS dv
                   WHERE dv.dataset_id = OLD.id
                     AND (
                         dv.status = 'ready'
                         OR EXISTS (
                             SELECT 1
                             FROM generation_run_dataset_versions AS rdv
                             JOIN generation_runs AS r ON r.id = rdv.run_id
                             WHERE rdv.dataset_version_id = dv.id
                               AND r.status = 'succeeded'
                         )
                     )
               )
            THEN
                RAISE EXCEPTION
                    'published dataset versions cannot be moved between projects';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_datasets_published_project
        BEFORE UPDATE OF project_id ON datasets
        FOR EACH ROW
        EXECUTE FUNCTION guard_published_dataset_project()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_datasets_published_project ON datasets")
    op.execute("DROP FUNCTION guard_published_dataset_project()")
    op.execute("DROP TRIGGER trg_projects_published_working_srid ON projects")
    op.execute("DROP FUNCTION guard_published_project_working_srid()")
    op.execute("DROP TRIGGER trg_dataset_versions_terminal_ready ON dataset_versions")
    op.execute("DROP FUNCTION guard_published_dataset_version_status()")
