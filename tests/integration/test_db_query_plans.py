"""PostGIS EXPLAIN ANALYZE gate with no durable seeded rows."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.session import engine
from backend.app.models.generated_entity import GeneratedRoad
from backend.app.models.job import Job
from backend.app.models.source_layer import SourceRoad
from benchmarks.db_query_plans import _plan_nodes, profile_queries


def test_nested_plan_parser_traverses_all_child_scans() -> None:
    root = {
        "Node Type": "Limit",
        "Plans": [
            {"Node Type": "Index Scan", "Index Name": "ix_source_roads_geometry"},
            {"Node Type": "Bitmap Heap Scan", "Plans": [
                {"Node Type": "Bitmap Index Scan", "Index Name": "ix_source_roads_id"},
            ]},
        ],
    }
    assert [node["Node Type"] for node in _plan_nodes(root)] == [
        "Limit", "Index Scan", "Bitmap Heap Scan", "Bitmap Index Scan",
    ]


def test_real_explain_is_rollback_only_and_queries_match_adapters() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    with Session(engine) as session:
        before = (
            session.scalar(select(func.count()).select_from(SourceRoad)),
            session.scalar(select(func.count()).select_from(GeneratedRoad)),
            session.scalar(select(func.count()).select_from(Job)),
        )

    report = profile_queries(rows=200)

    assert report["profile_schema"] == "s14-query-profiles-v1"
    assert report["fixture"] == "synthetic rollback-only"
    assert report["rows_per_spatial_owner"] == 200
    profiles = report["profiles"]
    assert [profile["name"] for profile in profiles] == [
        "source_roads_narrow",
        "source_roads_wide",
        "generated_roads_narrow",
        "generated_roads_wide",
        "active_job_admission",
        "latest_project_runs",
    ]
    for profile in profiles:
        assert profile["execution_ms"] >= 0
        assert profile["planning_ms"] >= 0
        assert "Plan" in profile["explain"]
        assert profile["sql"].lstrip().upper().startswith("SELECT")
        assert isinstance(profile["plan_nodes"], list)
        assert profile["plan_nodes"]
    assert profiles[0]["observed_rows"] == 0  # 200 roads in the first grid row
    assert profiles[1]["observed_rows"] > 0
    assert profiles[3]["observed_rows"] > 0

    with Session(engine) as session:
        after = (
            session.scalar(select(func.count()).select_from(SourceRoad)),
            session.scalar(select(func.count()).select_from(GeneratedRoad)),
            session.scalar(select(func.count()).select_from(Job)),
        )
    assert after == before
