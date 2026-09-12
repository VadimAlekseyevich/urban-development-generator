"""Persist explicit project working SRID and boundary metadata."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_project_persistence"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("working_srid", sa.Integer(), nullable=True))
    op.add_column(
        "projects",
        sa.Column(
            "boundary_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.execute(
        """
        UPDATE projects
        SET working_srid = CASE
            WHEN working_crs ~ '^[0-9]+$' THEN working_crs::integer
            WHEN upper(working_crs) ~ '^EPSG:[0-9]+$'
                THEN split_part(upper(working_crs), ':', 2)::integer
            WHEN boundary IS NOT NULL AND ST_SRID(boundary) > 0 THEN ST_SRID(boundary)
            ELSE NULL
        END
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM projects WHERE working_srid IS NULL) THEN
                RAISE EXCEPTION
                    'Cannot migrate projects with unresolved working_crs; set an explicit EPSG SRID first';
            END IF;
        END
        $$;
        """
    )

    op.alter_column("projects", "working_srid", nullable=False)
    op.create_check_constraint(
        "ck_projects_working_srid_positive",
        "projects",
        "working_srid > 0",
    )
    op.drop_column("projects", "working_crs")


def downgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "working_crs",
            sa.String(length=64),
            nullable=True,
            server_default="AUTO",
        ),
    )
    op.execute("UPDATE projects SET working_crs = 'EPSG:' || working_srid::text")
    op.alter_column("projects", "working_crs", nullable=False)

    op.drop_constraint("ck_projects_working_srid_positive", "projects", type_="check")
    op.drop_column("projects", "boundary_metadata")
    op.drop_column("projects", "working_srid")
