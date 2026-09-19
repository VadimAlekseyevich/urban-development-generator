"""Add typed GeneratedBuilding persistence fields and indexes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_building_persistence"
down_revision = "0015_block_parcel_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_buildings",
        sa.Column("building_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("source_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("block_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("parcel_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("zone_class", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("archetype", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("building_use", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("floors", sa.Integer(), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("footprint_area_m2", sa.Float(), nullable=True),
    )
    op.add_column(
        "generated_buildings",
        sa.Column("gfa_m2", sa.Float(), nullable=True),
    )

    op.create_foreign_key(
        "fk_generated_buildings_block_id_generated_blocks",
        "generated_buildings",
        "generated_blocks",
        ["block_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_generated_buildings_parcel_id_generated_parcels",
        "generated_buildings",
        "generated_parcels",
        ["parcel_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_check_constraint(
        "ck_generated_buildings_building_key_nonempty",
        "generated_buildings",
        "building_key IS NULL OR length(btrim(building_key)) > 0",
    )
    op.create_check_constraint(
        "ck_generated_buildings_source_id_nonempty",
        "generated_buildings",
        "source_id IS NULL OR length(btrim(source_id)) > 0",
    )
    op.create_check_constraint(
        "ck_generated_buildings_zone_class",
        "generated_buildings",
        "zone_class IS NULL OR zone_class IN "
        "('residential', 'mixed', 'public', 'recreation')",
    )
    op.create_check_constraint(
        "ck_generated_buildings_archetype",
        "generated_buildings",
        "archetype IS NULL OR archetype IN "
        "('detached', 'point', 'bar', 'perimeter', 'courtyard', "
        "'public', 'commercial')",
    )
    op.create_check_constraint(
        "ck_generated_buildings_use",
        "generated_buildings",
        "building_use IS NULL OR building_use IN "
        "('residential', 'mixed', 'public', 'commercial')",
    )
    op.create_check_constraint(
        "ck_generated_buildings_floors_positive",
        "generated_buildings",
        "floors IS NULL OR floors > 0",
    )
    op.create_check_constraint(
        "ck_generated_buildings_footprint_area_positive",
        "generated_buildings",
        "footprint_area_m2 IS NULL OR footprint_area_m2 > 0",
    )
    op.create_check_constraint(
        "ck_generated_buildings_gfa_positive",
        "generated_buildings",
        "gfa_m2 IS NULL OR gfa_m2 > 0",
    )
    op.create_check_constraint(
        "ck_generated_buildings_parcel_requires_block",
        "generated_buildings",
        "parcel_id IS NULL OR block_id IS NOT NULL",
    )

    op.create_index(
        "uq_generated_buildings_run_building_key",
        "generated_buildings",
        ["run_id", "building_key"],
        unique=True,
    )
    op.create_index(
        "ix_generated_buildings_run_block_id",
        "generated_buildings",
        ["run_id", "block_id"],
    )
    op.create_index(
        "ix_generated_buildings_run_parcel_id",
        "generated_buildings",
        ["run_id", "parcel_id"],
    )
    op.create_index(
        "ix_generated_buildings_run_source_id",
        "generated_buildings",
        ["run_id", "source_id"],
    )
    op.create_index(
        "ix_generated_buildings_run_use",
        "generated_buildings",
        ["run_id", "building_use"],
    )
    op.create_index(
        "ix_generated_buildings_run_archetype",
        "generated_buildings",
        ["run_id", "archetype"],
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE generated_buildings
        SET attributes_json = attributes_json || jsonb_strip_nulls(jsonb_build_object(
            'building_key', building_key,
            'source_id', source_id,
            'block_id', block_id::text,
            'parcel_id', parcel_id::text,
            'zone_class', zone_class,
            'archetype', archetype,
            'building_use', building_use,
            'floors', floors,
            'footprint_area_m2', footprint_area_m2,
            'gfa_m2', gfa_m2
        ))
        """
    )

    op.drop_index(
        "ix_generated_buildings_run_archetype",
        table_name="generated_buildings",
    )
    op.drop_index(
        "ix_generated_buildings_run_use",
        table_name="generated_buildings",
    )
    op.drop_index(
        "ix_generated_buildings_run_source_id",
        table_name="generated_buildings",
    )
    op.drop_index(
        "ix_generated_buildings_run_parcel_id",
        table_name="generated_buildings",
    )
    op.drop_index(
        "ix_generated_buildings_run_block_id",
        table_name="generated_buildings",
    )
    op.drop_index(
        "uq_generated_buildings_run_building_key",
        table_name="generated_buildings",
    )

    op.drop_constraint(
        "ck_generated_buildings_parcel_requires_block",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_buildings_gfa_positive",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_buildings_footprint_area_positive",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_buildings_floors_positive",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_buildings_use",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_buildings_archetype",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_buildings_zone_class",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_buildings_source_id_nonempty",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_buildings_building_key_nonempty",
        "generated_buildings",
        type_="check",
    )
    op.drop_constraint(
        "fk_generated_buildings_parcel_id_generated_parcels",
        "generated_buildings",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_generated_buildings_block_id_generated_blocks",
        "generated_buildings",
        type_="foreignkey",
    )

    op.drop_column("generated_buildings", "gfa_m2")
    op.drop_column("generated_buildings", "footprint_area_m2")
    op.drop_column("generated_buildings", "floors")
    op.drop_column("generated_buildings", "building_use")
    op.drop_column("generated_buildings", "archetype")
    op.drop_column("generated_buildings", "zone_class")
    op.drop_column("generated_buildings", "parcel_id")
    op.drop_column("generated_buildings", "block_id")
    op.drop_column("generated_buildings", "source_id")
    op.drop_column("generated_buildings", "building_key")
