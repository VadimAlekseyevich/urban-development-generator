"""Add persisted single-layer GeoJSON export requests (S13-T07)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_geojson_exports"
down_revision = "0023_published_tile_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geojson_exports",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("layer_id", sa.String(length=128), nullable=False),
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
        sa.Column("max_features", sa.Integer(), nullable=False, server_default="100000"),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "layer_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'",
            name="ck_geojson_exports_layer_id",
        ),
        sa.CheckConstraint(
            "(dataset_version_id IS NOT NULL) <> (run_id IS NOT NULL)",
            name="ck_geojson_exports_exact_owner",
        ),
        sa.CheckConstraint(
            "bbox_west >= -180 AND bbox_west < bbox_east AND bbox_east <= 180",
            name="ck_geojson_exports_longitude_bounds",
        ),
        sa.CheckConstraint(
            "bbox_south >= -90 AND bbox_south < bbox_north AND bbox_north <= 90",
            name="ck_geojson_exports_latitude_bounds",
        ),
        sa.CheckConstraint(
            "max_features > 0 AND max_features <= 100000",
            name="ck_geojson_exports_max_features",
        ),
        sa.UniqueConstraint("artifact_id", name="uq_geojson_exports_artifact_id"),
    )
    op.create_index(
        "ix_geojson_exports_dataset_version_id",
        "geojson_exports",
        ["dataset_version_id"],
    )
    op.create_index("ix_geojson_exports_run_id", "geojson_exports", ["run_id"])


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM geojson_exports) THEN
                RAISE EXCEPTION 'Cannot downgrade GeoJSON exports while rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_geojson_exports_run_id", table_name="geojson_exports")
    op.drop_index(
        "ix_geojson_exports_dataset_version_id",
        table_name="geojson_exports",
    )
    op.drop_table("geojson_exports")
