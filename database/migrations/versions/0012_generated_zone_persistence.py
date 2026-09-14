"""Specialize GeneratedZone persistence fields for functional zoning."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_generated_zone_persistence"
down_revision = "0011_spatial_indexes"
branch_labels = None
depends_on = None

_ZONE_CLASSES = "'residential', 'mixed', 'public', 'recreation'"


def upgrade() -> None:
    op.add_column(
        "generated_zones",
        sa.Column("zone_class", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "generated_zones",
        sa.Column("area_m2", sa.Float(), nullable=True),
    )
    op.add_column(
        "generated_zones",
        sa.Column(
            "diagnostics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # Preserve any pre-T08 generic rows when they already carried the semantic class
    # in attributes_json. Area is safely derived from the metric run geometry.
    op.execute(
        f"""
        UPDATE generated_zones
        SET zone_class = attributes_json ->> 'zone_class'
        WHERE attributes_json ->> 'zone_class' IN ({_ZONE_CLASSES})
        """
    )
    op.execute(
        """
        UPDATE generated_zones
        SET area_m2 = ST_Area(geometry)
        WHERE area_m2 IS NULL
        """
    )
    op.execute(
        """
        UPDATE generated_zones
        SET diagnostics_json = attributes_json -> 'diagnostics'
        WHERE jsonb_typeof(attributes_json -> 'diagnostics') = 'object'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM generated_zones
                WHERE zone_class IS NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot migrate legacy generated_zones without a valid attributes_json.zone_class';
            END IF;
        END
        $$;
        """
    )

    op.alter_column("generated_zones", "zone_class", nullable=False)
    op.alter_column("generated_zones", "area_m2", nullable=False)
    op.create_check_constraint(
        "ck_generated_zones_zone_class",
        "generated_zones",
        f"zone_class IN ({_ZONE_CLASSES})",
    )
    op.create_check_constraint(
        "ck_generated_zones_area_positive",
        "generated_zones",
        "area_m2 > 0",
    )
    op.create_check_constraint(
        "ck_generated_zones_diagnostics_object",
        "generated_zones",
        "jsonb_typeof(diagnostics_json) = 'object'",
    )


def downgrade() -> None:
    # Keep T08 semantics in the generic JSON payload so downgrade does not silently
    # discard the zoning class, area or diagnostics of existing rows.
    op.execute(
        """
        UPDATE generated_zones
        SET attributes_json = attributes_json || jsonb_build_object(
            'zone_class', zone_class,
            'area_m2', area_m2,
            'diagnostics', diagnostics_json
        )
        """
    )

    op.drop_constraint(
        "ck_generated_zones_diagnostics_object",
        "generated_zones",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_zones_area_positive",
        "generated_zones",
        type_="check",
    )
    op.drop_constraint(
        "ck_generated_zones_zone_class",
        "generated_zones",
        type_="check",
    )
    op.drop_column("generated_zones", "diagnostics_json")
    op.drop_column("generated_zones", "area_m2")
    op.drop_column("generated_zones", "zone_class")
