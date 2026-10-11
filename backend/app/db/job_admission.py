"""Database-authoritative project job admission across all producer endpoints.

Call only while holding the same exclusive Project row lock that serializes
all writers. Queued/running jobs consume slots; terminal jobs do not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.job import Job

ACTIVE_STATUSES = ("queued", "running")
RETRY_AFTER_SECONDS = 30

_GENERATION_TYPES = frozenset({"generation_run"})
_INGEST_TYPES = frozenset({"ingest_dataset"})
_EXPORT_TYPES = frozenset({"geojson_export", "geopackage_export"})


class JobCapacityExceeded(ValueError):
    """Reject work before inserting a partially configured Run or Job."""

    def __init__(self, *, scope: str, limit: int) -> None:
        self.scope = scope
        self.limit = limit
        super().__init__(f"active {scope} jobs have reached the configured limit of {limit}")


@dataclass(frozen=True, slots=True)
class JobAdmissionLimits:
    project: int
    generation: int
    ingest: int
    export: int

    @classmethod
    def from_settings(cls) -> JobAdmissionLimits:
        return cls(
            project=settings.max_active_jobs_per_project,
            generation=settings.max_active_generation_jobs_per_project,
            ingest=settings.max_active_ingest_jobs_per_project,
            export=settings.max_active_export_jobs_per_project,
        )

    def __post_init__(self) -> None:
        for name in ("project", "generation", "ingest", "export"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 128:
                raise ValueError(f"{name} capacity must be an integer between 1 and 128")


def enforce_project_job_capacity(
    session: Session,
    *,
    project_id: uuid.UUID,
    job_type: str,
    additional_jobs: int = 1,
    limits: JobAdmissionLimits | None = None,
) -> None:
    """Check aggregate and class-specific slots under the caller's project lock.

    No extra project lock is acquired here: all submitters must first lock
    Project(id) FOR UPDATE, before acquiring dataset/artifact/run row locks.
    This prevents two concurrent requests from accepting the last available
    slot, while preserving a consistent lock acquisition order.
    """
    if type(additional_jobs) is not int or not 1 <= additional_jobs <= 10:
        raise ValueError("additional_jobs must be between 1 and 10")
    if not isinstance(project_id, uuid.UUID):
        raise TypeError("project_id must be UUID")
    if job_type in _GENERATION_TYPES:
        scope = "generation"
        category = _GENERATION_TYPES
    elif job_type in _INGEST_TYPES:
        scope = "ingest"
        category = _INGEST_TYPES
    elif job_type in _EXPORT_TYPES:
        scope = "export"
        category = _EXPORT_TYPES
    else:
        raise ValueError("unknown background job type for admission")

    bounds = limits or JobAdmissionLimits.from_settings()
    counts = session.execute(
        select(Job.job_type, func.count())
        .where(Job.project_id == project_id, Job.status.in_(ACTIVE_STATUSES))
        .group_by(Job.job_type)
    ).all()
    by_type = {name: int(count) for name, count in counts}
    total = sum(by_type.values())
    if total + additional_jobs > bounds.project:
        raise JobCapacityExceeded(scope="project", limit=bounds.project)
    active_category = sum(by_type.get(name, 0) for name in category)
    category_limit: int = getattr(bounds, scope)
    if active_category + additional_jobs > category_limit:
        raise JobCapacityExceeded(scope=scope, limit=category_limit)
