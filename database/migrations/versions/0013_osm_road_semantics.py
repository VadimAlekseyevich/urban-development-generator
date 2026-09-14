"""Add normalized OSM crossing and traversal semantics to source roads."""

import sqlalchemy as sa
from alembic import op

revision = "0013_osm_road_semantics"
down_revision = "0012_generated_zone_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_roads",
        sa.Column(
            "one_way_direction",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'both'"),
        ),
    )
    op.add_column(
        "source_roads",
        sa.Column(
            "bridge",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "source_roads",
        sa.Column(
            "tunnel",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "source_roads",
        sa.Column(
            "layer",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # Existing OSM rows already retain their raw tags in attributes_json, so backfill the
    # normalized semantics without mutating dataset ownership or source geometry.
    op.execute(
        """
        UPDATE source_roads
        SET
            bridge = CASE
                WHEN NULLIF(BTRIM(attributes_json #>> '{osm_tags,bridge}'), '') IS NULL
                    THEN false
                ELSE LOWER(BTRIM(attributes_json #>> '{osm_tags,bridge}'))
                    NOT IN ('no', 'false', '0')
            END,
            tunnel = CASE
                WHEN NULLIF(BTRIM(attributes_json #>> '{osm_tags,tunnel}'), '') IS NULL
                    THEN false
                ELSE LOWER(BTRIM(attributes_json #>> '{osm_tags,tunnel}'))
                    NOT IN ('no', 'false', '0')
            END,
            layer = CASE
                WHEN BTRIM(attributes_json #>> '{osm_tags,layer}') ~ '^[+-]?[0-9]{1,10}$'
                    THEN CASE
                        WHEN CAST(BTRIM(attributes_json #>> '{osm_tags,layer}') AS bigint)
                            BETWEEN -2147483648 AND 2147483647
                            THEN CAST(
                                BTRIM(attributes_json #>> '{osm_tags,layer}') AS integer
                            )
                        ELSE 0
                    END
                ELSE 0
            END,
            one_way_direction = CASE
                WHEN LOWER(BTRIM(attributes_json #>> '{osm_tags,oneway}'))
                    IN ('yes', 'true', '1') THEN 'forward'
                WHEN LOWER(BTRIM(attributes_json #>> '{osm_tags,oneway}'))
                    IN ('-1', 'reverse') THEN 'reverse'
                WHEN LOWER(BTRIM(attributes_json #>> '{osm_tags,oneway}'))
                    IN ('no', 'false', '0') THEN 'both'
                WHEN NULLIF(BTRIM(attributes_json #>> '{osm_tags,oneway}'), '') IS NULL
                 AND (
                    LOWER(BTRIM(attributes_json #>> '{osm_tags,junction}'))
                        IN ('roundabout', 'circular')
                    OR LOWER(BTRIM(attributes_json #>> '{osm_tags,highway}'))
                        IN ('motorway', 'motorway_link')
                 ) THEN 'forward'
                WHEN one_way THEN 'forward'
                ELSE 'both'
            END
        WHERE jsonb_typeof(attributes_json -> 'osm_tags') = 'object'
        """
    )
    op.execute(
        """
        UPDATE source_roads
        SET one_way = (one_way_direction <> 'both')
        """
    )

    op.create_check_constraint(
        "ck_source_roads_one_way_direction",
        "source_roads",
        "one_way_direction IN ('both', 'forward', 'reverse')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_source_roads_one_way_direction",
        "source_roads",
        type_="check",
    )
    op.drop_column("source_roads", "layer")
    op.drop_column("source_roads", "tunnel")
    op.drop_column("source_roads", "bridge")
    op.drop_column("source_roads", "one_way_direction")
