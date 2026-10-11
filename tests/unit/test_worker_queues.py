"""Queue routing and independent worker role regression gates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings, settings
from worker.queues import LEGACY_QUEUE, REDIS_QUEUES, TASK_QUEUES, queue_for_task
from worker.settings import (
    AnalysisWorkerSettings,
    ExportWorkerSettings,
    GenerationWorkerSettings,
    IngestWorkerSettings,
    LegacyWorkerSettings,
)


def test_queues_are_distinct_and_match_authoritative_tasks() -> None:
    assert set(REDIS_QUEUES) == {"ingest", "generation", "analysis", "export"}
    assert len(set(REDIS_QUEUES.values())) == 4
    assert LEGACY_QUEUE not in REDIS_QUEUES.values()
    assert TASK_QUEUES == {
        "run_ingest": "ingest",
        "run_generation": "generation",
        "run_geojson_export": "export",
        "run_geopackage_export": "export",
    }
    for task, logical in TASK_QUEUES.items():
        assert queue_for_task(task=task, logical_queue=logical) == REDIS_QUEUES[logical]
        # Existing pending DB rows with the old "default" logical name
        # use the matching task-owned worker, not an overloaded shared queue.
        assert queue_for_task(task=task, logical_queue="default") == REDIS_QUEUES[logical]


@pytest.mark.parametrize(
    ("task", "logical"),
    [
        ("run_generation", "export"),
        ("run_ingest", "generation"),
        ("run_geojson_export", "ingest"),
        ("run_geopackage_export", "analysis"),
        ("nonexistent", "default"),
        ("run_generation", "unconfigured"),
    ],
)
def test_misrouted_or_unknown_tasks_fail_closed(task: str, logical: str) -> None:
    with pytest.raises(ValueError):
        queue_for_task(task=task, logical_queue=logical)


def test_worker_roles_register_only_owned_tasks_and_have_independent_limits() -> None:
    by_role = {
        "ingest": (IngestWorkerSettings, {"run_ingest"}, settings.worker_ingest_max_jobs),
        "generation": (
            GenerationWorkerSettings, {"run_generation"}, settings.worker_generation_max_jobs
        ),
        "export": (
            ExportWorkerSettings,
            {"run_geojson_export", "run_geopackage_export"},
            settings.worker_export_max_jobs,
        ),
    }
    for role, (cls, expected, limit) in by_role.items():
        assert cls.queue_name == REDIS_QUEUES[role]
        assert {fn.__name__ for fn in cls.functions} == expected
        assert cls.max_jobs == limit
        assert 1 <= cls.max_jobs <= 32
    assert AnalysisWorkerSettings.queue_name == REDIS_QUEUES["analysis"]
    assert AnalysisWorkerSettings.max_jobs == settings.worker_analysis_max_jobs
    assert len(AnalysisWorkerSettings.functions) == 1
    # ARQ's settings loader reads concrete class attributes. An inherited
    # Redis DSN silently fell back to localhost in Docker before this gate.
    for role_class in (
        IngestWorkerSettings, GenerationWorkerSettings, AnalysisWorkerSettings,
        ExportWorkerSettings, LegacyWorkerSettings,
    ):
        assert "redis_settings" in vars(role_class)
        assert "job_timeout" in vars(role_class)
        assert role_class.redis_settings == IngestWorkerSettings.redis_settings
        assert role_class.job_timeout == 60 * 60
    assert LegacyWorkerSettings.queue_name == LEGACY_QUEUE
    assert LegacyWorkerSettings.max_jobs == 1
    legacy_tasks = {fn.__name__ for fn in LegacyWorkerSettings.functions}
    assert legacy_tasks == set(TASK_QUEUES) | {"gc_orphan_artifacts"}


def test_invalid_concurrency_is_rejected_at_startup() -> None:
    with pytest.raises(ValidationError):
        Settings(worker_generation_max_jobs=0)
    with pytest.raises(ValidationError):
        Settings(worker_export_max_jobs=33)
