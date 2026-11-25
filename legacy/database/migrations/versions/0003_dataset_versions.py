"""Split logical datasets from immutable dataset versions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_dataset_versions"
down_revision = "0002_project_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_dataset_versions_version_positive",
        ),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dataset_versions_checksum_sha256",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_dataset_versions_dataset_version",
        ),
    )
    op.create_index(
        "ix_dataset_versions_dataset_id",
        "dataset_versions",
        ["dataset_id"],
    )
    op.create_index(
        "ix_dataset_versions_status",
        "dataset_versions",
        ["status"],
    )

    op.execute(
        """
        INSERT INTO dataset_versions (
            id,
            dataset_id,
            version,
            checksum_sha256,
            status,
            source_metadata,
            created_at
        )
        SELECT
            id,
            id,
            1,
            CASE
                WHEN lower(metadata_json ->> 'checksum_sha256')
                    ~ '^[0-9a-f]{64}$'
                    THEN lower(metadata_json ->> 'checksum_sha256')
                ELSE NULL
            END,
            status,
            jsonb_strip_nulls(
                jsonb_build_object(
                    'source', source,
                    'original_crs', original_crs,
                    'working_crs', working_crs,
                    'legacy_storage_path', storage_path,
                    'metadata', metadata_json
                )
            ),
            created_at
        FROM datasets
        """
    )

    op.execute(
        """
        CREATE FUNCTION prevent_dataset_version_content_update()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
                OR NEW.version IS DISTINCT FROM OLD.version
                OR NEW.checksum_sha256 IS DISTINCT FROM OLD.checksum_sha256
                OR NEW.source_metadata IS DISTINCT FROM OLD.source_metadata
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION
                    'dataset version identity/content is immutable; create a new version instead';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dataset_versions_immutable_content
        BEFORE UPDATE ON dataset_versions
        FOR EACH ROW
        EXECUTE FUNCTION prevent_dataset_version_content_update()
        """
    )

    op.drop_index("ix_datasets_status", table_name="datasets")
    op.drop_column("datasets", "source")
    op.drop_column("datasets", "original_crs")
    op.drop_column("datasets", "working_crs")
    op.drop_column("datasets", "status")
    op.drop_column("datasets", "storage_path")
    op.drop_column("datasets", "metadata_json")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM dataset_versions
                WHERE version <> 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade dataset versioning while versions beyond v1 exist';
            END IF;
        END
        $$;
        """
    )

    op.add_column(
        "datasets",
        sa.Column("source", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "datasets",
        sa.Column("original_crs", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "datasets",
        sa.Column("working_crs", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "datasets",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="uploaded",
        ),
    )
    op.add_column(
        "datasets",
        sa.Column("storage_path", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "datasets",
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.execute(
        """
        UPDATE datasets AS d
        SET
            source = dv.source_metadata ->> 'source',
            original_crs = dv.source_metadata ->> 'original_crs',
            working_crs = dv.source_metadata ->> 'working_crs',
            status = dv.status,
            storage_path = dv.source_metadata ->> 'legacy_storage_path',
            metadata_json = COALESCE(
                dv.source_metadata -> 'metadata',
                '{}'::jsonb
            ) || (
                dv.source_metadata
                - 'source'
                - 'original_crs'
                - 'working_crs'
                - 'legacy_storage_path'
                - 'metadata'
            )
        FROM dataset_versions AS dv
        WHERE dv.dataset_id = d.id
          AND dv.version = 1
        """
    )
    op.create_index("ix_datasets_status", "datasets", ["status"])

    op.execute(
        "DROP TRIGGER trg_dataset_versions_immutable_content ON dataset_versions"
    )
    op.execute("DROP FUNCTION prevent_dataset_version_content_update()")
    op.drop_index(
        "ix_dataset_versions_status",
        table_name="dataset_versions",
    )
    op.drop_index(
        "ix_dataset_versions_dataset_id",
        table_name="dataset_versions",
    )
    op.drop_table("dataset_versions")
