"""Add run-scoped generated spatial entity tables."""

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_generated_entities"
down_revision = "0008_job_outbox"
branch_labels = None
depends_on = None

_GENERATED_TABLE_GEOMETRY_TYPES = (
    ("generated_zones", "MULTIPOLYGON"),
    ("generated_roads", "LINESTRING"),
    ("generated_blocks", "POLYGON"),
    ("generated_parcels", "POLYGON"),
    ("generated_buildings", "POLYGON"),
    ("generated_infrastructure", "GEOMETRY"),
)


def _create_generated_table(table_name: str, geometry_type: str) -> None:
    op.create_table(
        table_name,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type=geometry_type,
                srid=-1,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "attributes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name=f"ck_{table_name}_attributes_object",
        ),
    )


def upgrade() -> None:
    for table_name, geometry_type in _GENERATED_TABLE_GEOMETRY_TYPES:
        _create_generated_table(table_name, geometry_type)

    op.execute(
        """
        CREATE FUNCTION guard_generated_entity_row()
        RETURNS trigger AS $$
        DECLARE
            target_run_id uuid;
            target_run_status varchar;
            target_working_srid integer;
        BEGIN
            IF TG_OP = 'UPDATE' AND NEW.run_id <> OLD.run_id THEN
                RAISE EXCEPTION 'generated entity run_id is immutable';
            END IF;

            target_run_id := CASE
                WHEN TG_OP = 'DELETE' THEN OLD.run_id
                ELSE NEW.run_id
            END;

            SELECT status, working_srid
            INTO target_run_status, target_working_srid
            FROM generation_runs
            WHERE id = target_run_id;

            IF target_run_status = 'succeeded' THEN
                RAISE EXCEPTION
                    'generated entities of a successful generation run are immutable';
            END IF;

            IF TG_OP <> 'DELETE' THEN
                IF ST_SRID(NEW.geometry) <> target_working_srid THEN
                    RAISE EXCEPTION
                        'generated entity geometry SRID must match run working_srid';
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

    for table_name, _geometry_type in _GENERATED_TABLE_GEOMETRY_TYPES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_run_guard
            BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION guard_generated_entity_row()
            """
        )


def downgrade() -> None:
    table_union = " UNION ALL ".join(
        f"SELECT 1 FROM {table_name}"
        for table_name, _geometry_type in _GENERATED_TABLE_GEOMETRY_TYPES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS ({table_union}) THEN
                RAISE EXCEPTION
                    'Cannot downgrade generated entity schema while generated rows exist';
            END IF;
        END
        $$;
        """
    )

    for table_name, _geometry_type in reversed(_GENERATED_TABLE_GEOMETRY_TYPES):
        op.execute(f"DROP TRIGGER trg_{table_name}_run_guard ON {table_name}")
        op.drop_table(table_name)

    op.execute("DROP FUNCTION guard_generated_entity_row()")
