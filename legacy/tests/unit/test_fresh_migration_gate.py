"""Fail-closed release schema audit semantics without a running database."""

from __future__ import annotations

import pytest

from backend.app import models  # noqa: F401
from backend.app.db.base import Base
from scripts.fresh_migration_gate import REQUIRED_INDEXES, REQUIRED_TABLES, verify_schema


def test_release_schema_check_requires_disposable_db_name() -> None:
    with pytest.raises(ValueError, match="disposable"):
        verify_schema(phase="migrated", expected_database="urban_generator")
    with pytest.raises(ValueError, match="disposable"):
        verify_schema(phase="empty", expected_database="postgres")


def test_release_schema_check_rejects_unknown_phase_before_connecting() -> None:
    with pytest.raises(ValueError, match="phase"):
        verify_schema(phase="schema-only", expected_database="urban_generator_release_gate")


def test_release_schema_gate_covers_export_job_and_spatial_owners() -> None:
    assert REQUIRED_TABLES <= set(Base.metadata.tables)
    assert {"source_roads", "generated_roads"} <= set(Base.metadata.tables)
    assert len(REQUIRED_INDEXES) == 2
    assert "job_outbox" in REQUIRED_TABLES
    assert "run_stage_results" in REQUIRED_TABLES
