"""Add persisted multi-layer GeoPackage export requests (S13-T08)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_geopackage_exports"
down_revision = "0024_geojson_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geopackage_exports",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("layer_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("bbox_west", sa.Float(), nullable=False),
        sa.Column("bbox_south", sa.Float(), nullable=False),
        sa.Column("bbox_east", sa.Float(), nullable=False),
        sa.Column("bbox_north", sa.Float(), nullable=False),
        sa.Column(
            "max_features_per_layer",
            sa.Integer(),
            nullable=False,
            server_default="100000",
        ),
        sa.Column(
            "max_total_features",
            sa.Integer(),
            nullable=False,
            server_default="250000",
        ),
        sa.Column(
            "layer_feature_counts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(layer_ids_json) = 'array' "
            "AND jsonb_array_length(layer_ids_json) BETWEEN 1 AND 15",
            name="ck_geopackage_exports_layer_ids",
        ),
        sa.CheckConstraint(
            "dataset_version_id IS NOT NULL OR run_id IS NOT NULL",
            name="ck_geopackage_exports_owner_present",
        ),
        sa.CheckConstraint(
            "bbox_west >= -180 AND bbox_west < bbox_east AND bbox_east <= 180",
            name="ck_geopackage_exports_longitude_bounds",
        ),
        sa.CheckConstraint(
            "bbox_south >= -90 AND bbox_south < bbox_north AND bbox_north <= 90",
            name="ck_geopackage_exports_latitude_bounds",
        ),
        sa.CheckConstraint(
            "max_features_per_layer > 0 AND max_features_per_layer <= 100000",
            name="ck_geopackage_exports_per_layer_cap",
        ),
        sa.CheckConstraint(
            "max_total_features > 0 AND max_total_features <= 500000",
            name="ck_geopackage_exports_total_cap",
        ),
        sa.CheckConstraint(
            "layer_feature_counts_json IS NULL "
            "OR jsonb_typeof(layer_feature_counts_json) = 'object'",
            name="ck_geopackage_exports_feature_counts",
        ),
        sa.UniqueConstraint(
            "artifact_id",
            name="uq_geopackage_exports_artifact_id",
        ),
    )
    op.create_index(
        "ix_geopackage_exports_dataset_version_id",
        "geopackage_exports",
        ["dataset_version_id"],
    )
    op.create_index(
        "ix_geopackage_exports_run_id",
        "geopackage_exports",
        ["run_id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM geopackage_exports) THEN
                RAISE EXCEPTION 'Cannot downgrade GeoPackage exports while rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index(
        "ix_geopackage_exports_run_id",
        table_name="geopackage_exports",
    )
    op.drop_index(
        "ix_geopackage_exports_dataset_version_id",
        table_name="geopackage_exports",
    )
    op.drop_table("geopackage_exports")
