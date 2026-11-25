"""Add canonical normalized source layer tables."""

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_source_layers"
down_revision = "0009_generated_entities"
branch_labels = None
depends_on = None

_SOURCE_TABLES = (
    "source_roads",
    "source_buildings",
    "source_landuse",
    "source_water",
    "source_facilities",
    "source_constraints",
)


def _common_columns() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_feature_id", sa.String(length=255), nullable=True),
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
    ]


def _geometry(geometry_type: str) -> sa.Column[object]:
    return sa.Column(
        "geometry",
        geoalchemy2.Geometry(
            geometry_type=geometry_type,
            srid=-1,
            spatial_index=False,
        ),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "source_roads",
        *_common_columns(),
        sa.Column("road_class", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("lanes", sa.Integer(), nullable=True),
        sa.Column("max_speed_kph", sa.Float(), nullable=True),
        sa.Column(
            "one_way",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _geometry("MULTILINESTRING"),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_roads_attributes_object",
        ),
        sa.CheckConstraint(
            "lanes IS NULL OR lanes > 0",
            name="ck_source_roads_lanes_positive",
        ),
        sa.CheckConstraint(
            "max_speed_kph IS NULL OR max_speed_kph > 0",
            name="ck_source_roads_max_speed_positive",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_roads_version_feature",
        ),
    )

    op.create_table(
        "source_buildings",
        *_common_columns(),
        sa.Column("building_class", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("levels", sa.Integer(), nullable=True),
        sa.Column("height_m", sa.Float(), nullable=True),
        _geometry("MULTIPOLYGON"),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_buildings_attributes_object",
        ),
        sa.CheckConstraint(
            "levels IS NULL OR levels > 0",
            name="ck_source_buildings_levels_positive",
        ),
        sa.CheckConstraint(
            "height_m IS NULL OR height_m > 0",
            name="ck_source_buildings_height_positive",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_buildings_version_feature",
        ),
    )

    op.create_table(
        "source_landuse",
        *_common_columns(),
        sa.Column("landuse_class", sa.String(length=64), nullable=False),
        _geometry("MULTIPOLYGON"),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_landuse_attributes_object",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_landuse_version_feature",
        ),
    )

    op.create_table(
        "source_water",
        *_common_columns(),
        sa.Column("water_class", sa.String(length=64), nullable=False),
        _geometry("GEOMETRY"),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_water_attributes_object",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_water_version_feature",
        ),
    )

    op.create_table(
        "source_facilities",
        *_common_columns(),
        sa.Column("facility_class", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("capacity", sa.Float(), nullable=True),
        _geometry("GEOMETRY"),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_facilities_attributes_object",
        ),
        sa.CheckConstraint(
            "capacity IS NULL OR capacity >= 0",
            name="ck_source_facilities_capacity_nonnegative",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_facilities_version_feature",
        ),
    )

    op.create_table(
        "source_constraints",
        *_common_columns(),
        sa.Column("constraint_code", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        _geometry("GEOMETRY"),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_constraints_attributes_object",
        ),
        sa.CheckConstraint(
            "severity IN ('HARD', 'SOFT')",
            name="ck_source_constraints_severity",
        ),
        sa.CheckConstraint(
            "scope IN ('TERRITORY', 'ZONE', 'ROAD', 'BLOCK', 'PARCEL', "
            "'BUILDING', 'DEMOGRAPHY', 'INFRASTRUCTURE')",
            name="ck_source_constraints_scope",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_constraints_version_feature",
        ),
    )

    op.execute(
        """
        CREATE FUNCTION guard_source_entity_row()
        RETURNS trigger AS $$
        DECLARE
            target_dataset_version_id uuid;
            target_version_status varchar;
            target_working_srid integer;
        BEGIN
            IF TG_OP = 'UPDATE'
                AND NEW.dataset_version_id IS DISTINCT FROM OLD.dataset_version_id
            THEN
                RAISE EXCEPTION 'source entity dataset_version_id is immutable';
            END IF;

            target_dataset_version_id := CASE
                WHEN TG_OP = 'DELETE' THEN OLD.dataset_version_id
                ELSE NEW.dataset_version_id
            END;

            SELECT dv.status, p.working_srid
            INTO target_version_status, target_working_srid
            FROM dataset_versions AS dv
            JOIN datasets AS d ON d.id = dv.dataset_id
            JOIN projects AS p ON p.id = d.project_id
            WHERE dv.id = target_dataset_version_id;

            IF target_version_status = 'ready' THEN
                RAISE EXCEPTION
                    'source entities of a ready dataset version are immutable';
            END IF;

            IF TG_OP <> 'DELETE'
                AND ST_SRID(NEW.geometry) <> target_working_srid
            THEN
                RAISE EXCEPTION
                    'source entity geometry SRID must match project working_srid';
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    for table_name in _SOURCE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_version_guard
            BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION guard_source_entity_row()
            """
        )


def downgrade() -> None:
    table_union = " UNION ALL ".join(
        f"SELECT 1 FROM {table_name}" for table_name in _SOURCE_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS ({table_union}) THEN
                RAISE EXCEPTION
                    'Cannot downgrade canonical source layer schema while source rows exist';
            END IF;
        END
        $$;
        """
    )

    for table_name in reversed(_SOURCE_TABLES):
        op.execute(f"DROP TRIGGER trg_{table_name}_version_guard ON {table_name}")
        op.drop_table(table_name)

    op.execute("DROP FUNCTION guard_source_entity_row()")
