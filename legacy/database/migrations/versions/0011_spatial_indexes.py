"""Add spatial and scoped access indexes for persisted vector layers."""

from alembic import op

revision = "0011_spatial_indexes"
down_revision = "0010_source_layers"
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

_GENERATED_TABLES = (
    "generated_zones",
    "generated_roads",
    "generated_blocks",
    "generated_parcels",
    "generated_buildings",
    "generated_infrastructure",
)


def _create_scoped_indexes(
    table_name: str,
    scope_column: str,
) -> None:
    op.create_index(
        f"ix_{table_name}_{scope_column}_id",
        table_name,
        [scope_column, "id"],
    )
    op.create_index(
        f"ix_{table_name}_geometry",
        table_name,
        ["geometry"],
        postgresql_using="gist",
    )


def upgrade() -> None:
    for table_name in _SOURCE_TABLES:
        _create_scoped_indexes(table_name, "dataset_version_id")

    for table_name in _GENERATED_TABLES:
        _create_scoped_indexes(table_name, "run_id")


def downgrade() -> None:
    for table_name in reversed(_GENERATED_TABLES):
        op.drop_index(f"ix_{table_name}_geometry", table_name=table_name)
        op.drop_index(f"ix_{table_name}_run_id_id", table_name=table_name)

    for table_name in reversed(_SOURCE_TABLES):
        op.drop_index(f"ix_{table_name}_geometry", table_name=table_name)
        op.drop_index(
            f"ix_{table_name}_dataset_version_id_id",
            table_name=table_name,
        )
