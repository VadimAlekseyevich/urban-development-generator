"""Add typed GeneratedInfrastructure persistence fields and integrity rules."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_infra_persistence"
down_revision = "0017_stage_output_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_infrastructure",
        sa.Column("candidate_id", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column(
            "infrastructure_type_code",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column("category", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column("capacity", sa.Float(), nullable=True),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column("acceptance_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column("geometry_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column(
            "host_building_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column("site_area_m2", sa.Float(), nullable=True),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column("network_snapshot_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column("network_node_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "generated_infrastructure",
        sa.Column("network_snap_distance_m", sa.Float(), nullable=True),
    )

    op.create_foreign_key(
        "fk_generated_infrastructure_host_building",
        "generated_infrastructure",
        "generated_buildings",
        ["host_building_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_generated_infrastructure_candidate_id_nonempty",
        "generated_infrastructure",
        "candidate_id IS NULL OR length(btrim(candidate_id)) > 0",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_type_code_nonempty",
        "generated_infrastructure",
        "infrastructure_type_code IS NULL OR "
        "length(btrim(infrastructure_type_code)) > 0",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_category",
        "generated_infrastructure",
        "category IS NULL OR category IN "
        "('education', 'healthcare', 'retail', 'recreation')",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_capacity_positive",
        "generated_infrastructure",
        "capacity IS NULL OR capacity > 0",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_acceptance_index_nonnegative",
        "generated_infrastructure",
        "acceptance_index IS NULL OR acceptance_index >= 0",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_geometry_kind",
        "generated_infrastructure",
        "geometry_kind IS NULL OR geometry_kind IN ('site', 'host_building')",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_site_area_positive",
        "generated_infrastructure",
        "site_area_m2 IS NULL OR site_area_m2 > 0",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_network_snapshot_nonempty",
        "generated_infrastructure",
        "network_snapshot_id IS NULL OR "
        "length(btrim(network_snapshot_id)) > 0",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_network_node_nonempty",
        "generated_infrastructure",
        "network_node_id IS NULL OR length(btrim(network_node_id)) > 0",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_snap_distance_nonnegative",
        "generated_infrastructure",
        "network_snap_distance_m IS NULL OR network_snap_distance_m >= 0",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_geometry_shape",
        "generated_infrastructure",
        "geometry_kind IS NULL OR "
        "(geometry_kind = 'site' AND GeometryType(geometry) IN "
        "('POLYGON', 'MULTIPOLYGON')) OR "
        "(geometry_kind = 'host_building' AND GeometryType(geometry) = 'POINT')",
    )
    op.create_check_constraint(
        "ck_generated_infrastructure_typed_shape",
        "generated_infrastructure",
        "("
        "candidate_id IS NULL "
        "AND infrastructure_type_code IS NULL "
        "AND category IS NULL "
        "AND capacity IS NULL "
        "AND acceptance_index IS NULL "
        "AND geometry_kind IS NULL "
        "AND host_building_id IS NULL "
        "AND site_area_m2 IS NULL "
        "AND network_snapshot_id IS NULL "
        "AND network_node_id IS NULL "
        "AND network_snap_distance_m IS NULL"
        ") OR ("
        "candidate_id IS NOT NULL "
        "AND infrastructure_type_code IS NOT NULL "
        "AND category IS NOT NULL "
        "AND capacity IS NOT NULL "
        "AND acceptance_index IS NOT NULL "
        "AND geometry_kind IS NOT NULL "
        "AND network_snapshot_id IS NOT NULL "
        "AND network_node_id IS NOT NULL "
        "AND network_snap_distance_m IS NOT NULL "
        "AND ("
        "(geometry_kind = 'site' "
        "AND host_building_id IS NULL "
        "AND site_area_m2 IS NOT NULL) "
        "OR (geometry_kind = 'host_building' "
        "AND host_building_id IS NOT NULL "
        "AND site_area_m2 IS NULL)"
        ")"
        ")",
    )

    op.create_index(
        "uq_generated_infrastructure_run_candidate_type",
        "generated_infrastructure",
        ["run_id", "candidate_id", "infrastructure_type_code"],
        unique=True,
    )
    op.create_index(
        "uq_generated_infrastructure_run_type_acceptance",
        "generated_infrastructure",
        ["run_id", "infrastructure_type_code", "acceptance_index"],
        unique=True,
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE generated_infrastructure
        SET attributes_json = attributes_json || jsonb_strip_nulls(jsonb_build_object(
            'candidate_id', candidate_id,
            'infrastructure_type_code', infrastructure_type_code,
            'category', category,
            'capacity', capacity,
            'acceptance_index', acceptance_index,
            'geometry_kind', geometry_kind,
            'host_building_id', host_building_id::text,
            'site_area_m2', site_area_m2,
            'network_snapshot_id', network_snapshot_id,
            'network_node_id', network_node_id,
            'network_snap_distance_m', network_snap_distance_m
        ))
        """
    )

    op.drop_index(
        "uq_generated_infrastructure_run_type_acceptance",
        table_name="generated_infrastructure",
    )
    op.drop_index(
        "uq_generated_infrastructure_run_candidate_type",
        table_name="generated_infrastructure",
    )

    op.drop_constraint(
        "ck_generated_infrastructure_typed_shape",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_geometry_shape",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_snap_distance_nonnegative",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_network_node_nonempty",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_network_snapshot_nonempty",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_site_area_positive",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_geometry_kind",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_acceptance_index_nonnegative",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_capacity_positive",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_category",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_type_code_nonempty",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_infrastructure_candidate_id_nonempty",
        "generated_infrastructure",
        type_="check",
    )
    op.drop_constraint(
        "fk_generated_infrastructure_host_building",
        "generated_infrastructure",
        type_="foreignkey",
    )

    op.drop_column("generated_infrastructure", "network_snap_distance_m")
    op.drop_column("generated_infrastructure", "network_node_id")
    op.drop_column("generated_infrastructure", "network_snapshot_id")
    op.drop_column("generated_infrastructure", "site_area_m2")
    op.drop_column("generated_infrastructure", "host_building_id")
    op.drop_column("generated_infrastructure", "geometry_kind")
    op.drop_column("generated_infrastructure", "acceptance_index")
    op.drop_column("generated_infrastructure", "capacity")
    op.drop_column("generated_infrastructure", "category")
    op.drop_column("generated_infrastructure", "infrastructure_type_code")
    op.drop_column("generated_infrastructure", "candidate_id")
