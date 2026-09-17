"""Add semantic GeneratedBlock/GeneratedParcel persistence fields and indexes."""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision = "0015_block_parcel_persistence"
down_revision = "0014_generated_road_persistence"
branch_labels = None
depends_on = None

_ASSOCIATION_STATUSES = (
    "'ASSOCIATED', 'NO_OVERLAP', 'PARTIAL_OVERLAP', 'AMBIGUOUS_FULL_COVERAGE'"
)


def upgrade() -> None:
    op.add_column(
        "generated_blocks",
        sa.Column("block_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "generated_blocks",
        sa.Column("zone_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generated_blocks",
        sa.Column("area_m2", sa.Float(), nullable=True),
    )
    op.add_column(
        "generated_blocks",
        sa.Column("association_status", sa.String(length=32), nullable=True),
    )
    op.create_foreign_key(
        "fk_generated_blocks_zone_id_generated_zones",
        "generated_blocks",
        "generated_zones",
        ["zone_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_generated_blocks_block_key_nonempty",
        "generated_blocks",
        "block_key IS NULL OR length(btrim(block_key)) > 0",
    )
    op.create_check_constraint(
        "ck_generated_blocks_area_positive",
        "generated_blocks",
        "area_m2 IS NULL OR area_m2 > 0",
    )
    op.create_check_constraint(
        "ck_generated_blocks_association_status",
        "generated_blocks",
        f"association_status IS NULL OR association_status IN ({_ASSOCIATION_STATUSES})",
    )
    op.create_index(
        "uq_generated_blocks_run_block_key",
        "generated_blocks",
        ["run_id", "block_key"],
        unique=True,
    )
    op.create_index(
        "ix_generated_blocks_run_zone_id",
        "generated_blocks",
        ["run_id", "zone_id"],
    )
    op.create_index(
        "ix_generated_blocks_run_association_status",
        "generated_blocks",
        ["run_id", "association_status"],
    )

    op.add_column(
        "generated_parcels",
        sa.Column("parcel_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "generated_parcels",
        sa.Column("block_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generated_parcels",
        sa.Column("zone_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generated_parcels",
        sa.Column("area_m2", sa.Float(), nullable=True),
    )
    op.add_column(
        "generated_parcels",
        sa.Column("buildable_area_m2", sa.Float(), nullable=True),
    )
    op.add_column(
        "generated_parcels",
        sa.Column("frontage_m", sa.Float(), nullable=True),
    )
    op.add_column(
        "generated_parcels",
        sa.Column(
            "buildable_geometry",
            Geometry(geometry_type="GEOMETRY", srid=-1, spatial_index=False),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_generated_parcels_block_id_generated_blocks",
        "generated_parcels",
        "generated_blocks",
        ["block_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_generated_parcels_zone_id_generated_zones",
        "generated_parcels",
        "generated_zones",
        ["zone_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_generated_parcels_parcel_key_nonempty",
        "generated_parcels",
        "parcel_key IS NULL OR length(btrim(parcel_key)) > 0",
    )
    op.create_check_constraint(
        "ck_generated_parcels_area_positive",
        "generated_parcels",
        "area_m2 IS NULL OR area_m2 > 0",
    )
    op.create_check_constraint(
        "ck_generated_parcels_buildable_area_positive",
        "generated_parcels",
        "buildable_area_m2 IS NULL OR buildable_area_m2 > 0",
    )
    op.create_check_constraint(
        "ck_generated_parcels_frontage_nonnegative",
        "generated_parcels",
        "frontage_m IS NULL OR frontage_m >= 0",
    )
    op.create_index(
        "uq_generated_parcels_run_parcel_key",
        "generated_parcels",
        ["run_id", "parcel_key"],
        unique=True,
    )
    op.create_index(
        "ix_generated_parcels_run_block_id",
        "generated_parcels",
        ["run_id", "block_id"],
    )
    op.create_index(
        "ix_generated_parcels_run_zone_id",
        "generated_parcels",
        ["run_id", "zone_id"],
    )
    op.create_index(
        "ix_generated_parcels_buildable_geometry",
        "generated_parcels",
        ["buildable_geometry"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    # Preserve T10 semantic fields in generic JSON when downgrading to the
    # pre-specialized generated-entity schema.
    op.execute(
        """
        UPDATE generated_blocks
        SET attributes_json = attributes_json || jsonb_strip_nulls(jsonb_build_object(
            'block_key', block_key,
            'zone_id', zone_id::text,
            'area_m2', area_m2,
            'association_status', association_status
        ))
        """
    )
    op.execute(
        """
        UPDATE generated_parcels
        SET attributes_json = attributes_json || jsonb_strip_nulls(jsonb_build_object(
            'parcel_key', parcel_key,
            'block_id', block_id::text,
            'zone_id', zone_id::text,
            'area_m2', area_m2,
            'buildable_area_m2', buildable_area_m2,
            'frontage_m', frontage_m,
            'buildable_geometry_wkt', ST_AsText(buildable_geometry)
        ))
        """
    )

    op.drop_index(
        "ix_generated_parcels_buildable_geometry",
        table_name="generated_parcels",
    )
    op.drop_index("ix_generated_parcels_run_zone_id", table_name="generated_parcels")
    op.drop_index("ix_generated_parcels_run_block_id", table_name="generated_parcels")
    op.drop_index("uq_generated_parcels_run_parcel_key", table_name="generated_parcels")
    op.drop_constraint(
        "ck_generated_parcels_frontage_nonnegative",
        "generated_parcels",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_parcels_buildable_area_positive",
        "generated_parcels",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_parcels_area_positive",
        "generated_parcels",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_parcels_parcel_key_nonempty",
        "generated_parcels",
        type_="check",
    )
    op.drop_constraint(
        "fk_generated_parcels_zone_id_generated_zones",
        "generated_parcels",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_generated_parcels_block_id_generated_blocks",
        "generated_parcels",
        type_="foreignkey",
    )
    op.drop_column("generated_parcels", "buildable_geometry")
    op.drop_column("generated_parcels", "frontage_m")
    op.drop_column("generated_parcels", "buildable_area_m2")
    op.drop_column("generated_parcels", "area_m2")
    op.drop_column("generated_parcels", "zone_id")
    op.drop_column("generated_parcels", "block_id")
    op.drop_column("generated_parcels", "parcel_key")

    op.drop_index(
        "ix_generated_blocks_run_association_status",
        table_name="generated_blocks",
    )
    op.drop_index("ix_generated_blocks_run_zone_id", table_name="generated_blocks")
    op.drop_index("uq_generated_blocks_run_block_key", table_name="generated_blocks")
    op.drop_constraint(
        "ck_generated_blocks_association_status",
        "generated_blocks",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_blocks_area_positive",
        "generated_blocks",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_blocks_block_key_nonempty",
        "generated_blocks",
        type_="check",
    )
    op.drop_constraint(
        "fk_generated_blocks_zone_id_generated_zones",
        "generated_blocks",
        type_="foreignkey",
    )
    op.drop_column("generated_blocks", "association_status")
    op.drop_column("generated_blocks", "area_m2")
    op.drop_column("generated_blocks", "zone_id")
    op.drop_column("generated_blocks", "block_key")
