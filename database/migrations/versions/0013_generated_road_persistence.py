"""Add run-scoped semantic/source-ref indexes for GeneratedRoad persistence."""

from alembic import op

revision = "0013_generated_road_persistence"
down_revision = "0012_generated_zone_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_generated_roads_run_road_id
        ON generated_roads (run_id, (attributes_json ->> 'road_id'))
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ix_generated_roads_run_edge_id
        ON generated_roads (run_id, (attributes_json ->> 'edge_id'))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_generated_roads_run_road_class
        ON generated_roads (run_id, (attributes_json ->> 'road_class'))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_generated_roads_source_refs_gin
        ON generated_roads
        USING gin ((attributes_json -> 'source_road_ids'))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_generated_roads_source_refs_gin")
    op.execute("DROP INDEX IF EXISTS ix_generated_roads_run_road_class")
    op.execute("DROP INDEX IF EXISTS ix_generated_roads_run_edge_id")
    op.execute("DROP INDEX IF EXISTS ix_generated_roads_run_road_id")
