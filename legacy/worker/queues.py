"""Authoritative logical outbox routing and separate ARQ work queues.

A logical "default" in a historical pending outbox row maps by allowlisted
task type; new producers must emit one of the explicit logical queues. Redis
queue names are transport details, never persisted in JobOutbox.queue_name.
"""

from __future__ import annotations

from typing import Final

INGEST_QUEUE: Final = "ingest"
GENERATION_QUEUE: Final = "generation"
ANALYSIS_QUEUE: Final = "analysis"
EXPORT_QUEUE: Final = "export"
LEGACY_QUEUE: Final = "arq:queue"

REDIS_QUEUES: Final[dict[str, str]] = {
    INGEST_QUEUE: "arq:queue:ingest",
    GENERATION_QUEUE: "arq:queue:generation",
    ANALYSIS_QUEUE: "arq:queue:analysis",
    EXPORT_QUEUE: "arq:queue:export",
}

TASK_QUEUES: Final[dict[str, str]] = {
    "run_ingest": INGEST_QUEUE,
    "run_generation": GENERATION_QUEUE,
    "run_geojson_export": EXPORT_QUEUE,
    "run_geopackage_export": EXPORT_QUEUE,
}

# No analysis task exists yet; the analysis worker owns the hourly GC cron.
# Do not introduce a pretend analysis job just to populate a Redis queue.


def queue_for_task(*, task: str, logical_queue: str) -> str:
    """Fail closed on task/queue drift; support legacy default outbox rows."""

    expected = TASK_QUEUES.get(task)
    if expected is None:
        raise ValueError("unsupported outbox task")
    if logical_queue not in {expected, "default"}:
        raise ValueError(
            f"outbox task {task} must use {expected!r} logical queue "
            f"(got {logical_queue!r})"
        )
    return REDIS_QUEUES[expected]
