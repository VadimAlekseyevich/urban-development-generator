"""Enforce terminal published tile inputs at the database boundary (S13-T04).

A ready source dataset must never be reopened in place: its normalized
features were already protected by row triggers, but previously the status
could be changed back to processing. Published run inputs likewise must not
silently change their project working CRS or source version project owner.
"""

from alembic import op

revision = "0023_published_tile_inputs"
down_revision = "0022_exact_rerun_lineage"
_SOURCE_TABLES = (
    "source_roads", "source_buildings", "source_landuse",
    "source_water", "source_facilities", "source_constraints",
)
_GENERATED_TABLES = (
    "generated_zones", "generated_roads", "generated_blocks",
    "generated_parcels", "generated_buildings", "generated_infrastructure",
)

branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_published_dataset_version_status()
        RETURNS trigger AS $$
        BEGIN
            -- Serialize ready publication against project/dataset reassignment
            -- and working-CRS edits. A cache-immutable tile never races such
            -- a change after observing a committed ready status.
            IF NEW.status = 'ready' AND OLD.status IS DISTINCT FROM 'ready' THEN
                PERFORM 1
                FROM datasets AS d
                JOIN projects AS p ON p.id = d.project_id
                WHERE d.id = NEW.dataset_id
                FOR SHARE OF d, p;
            END IF;
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

    # The legacy row guards read parent status but do not lock the parent.
    # Lock it *before* each existing guard (PostgreSQL sorts same-event
    # trigger names) so in-flight writes cannot commit after ready/succeeded
    # publication and invalidate a long-lived immutable tile.
    op.execute(
        """
        CREATE FUNCTION lock_published_tile_parent()
        RETURNS trigger AS $$
        DECLARE
            source_version_id uuid;
            generated_run_id uuid;
        BEGIN
            IF TG_TABLE_NAME LIKE 'source_%' THEN
                source_version_id := CASE
                    WHEN TG_OP = 'DELETE' THEN OLD.dataset_version_id
                    ELSE NEW.dataset_version_id
                END;
                PERFORM 1 FROM dataset_versions
                WHERE id = source_version_id FOR SHARE;
            ELSE
                generated_run_id := CASE
                    WHEN TG_OP = 'DELETE' THEN OLD.run_id
                    ELSE NEW.run_id
                END;
                PERFORM 1 FROM generation_runs
                WHERE id = generated_run_id FOR SHARE;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (*_SOURCE_TABLES, *_GENERATED_TABLES):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_cache_parent_lock
            BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION lock_published_tile_parent()
            """
        )

    op.execute(
        """
        CREATE FUNCTION lock_published_run_dataset_refs()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                PERFORM 1 FROM generation_runs
                WHERE id = NEW.run_id FOR SHARE;
            ELSIF TG_OP = 'DELETE' THEN
                PERFORM 1 FROM generation_runs
                WHERE id = OLD.run_id FOR SHARE;
            ELSE
                PERFORM 1 FROM generation_runs
                WHERE id IN (OLD.run_id, NEW.run_id)
                ORDER BY id FOR SHARE;
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
        CREATE TRIGGER trg_generation_run_dataset_refs_cache_lock
        BEFORE INSERT OR UPDATE OR DELETE
        ON generation_run_dataset_versions
        FOR EACH ROW
        EXECUTE FUNCTION lock_published_run_dataset_refs()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_generation_run_dataset_refs_cache_lock "
        "ON generation_run_dataset_versions"
    )
    op.execute("DROP FUNCTION lock_published_run_dataset_refs()")
    for table_name in reversed((*_SOURCE_TABLES, *_GENERATED_TABLES)):
        op.execute(f"DROP TRIGGER trg_{table_name}_cache_parent_lock ON {table_name}")
    op.execute("DROP FUNCTION lock_published_tile_parent()")
    op.execute("DROP TRIGGER trg_datasets_published_project ON datasets")
    op.execute("DROP FUNCTION guard_published_dataset_project()")
    op.execute("DROP TRIGGER trg_projects_published_working_srid ON projects")
    op.execute("DROP FUNCTION guard_published_project_working_srid()")
    op.execute("DROP TRIGGER trg_dataset_versions_terminal_ready ON dataset_versions")
    op.execute("DROP FUNCTION guard_published_dataset_version_status()")
