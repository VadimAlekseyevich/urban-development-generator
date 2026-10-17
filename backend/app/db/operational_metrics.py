"""Bounded-cardinality, read-only operational metrics from authoritative tables.

Job/outbox/stage rows are shared between API/worker processes. Do not keep
process-local counters or expose project IDs/run IDs in Prometheus labels.
All metrics are gauges: authoritative rows may be archived or deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.stages.catalog import CANONICAL_STAGE_ORDER

JOB_TYPES: Final = (
    "generation_run", "ingest_dataset", "geojson_export", "geopackage_export"
)
JOB_STATUSES: Final = ("queued", "running", "succeeded", "failed", "cancelled")
OUTBOX_QUEUES: Final = ("default", "ingest", "generation", "analysis", "export")
OUTBOX_STATUSES: Final = ("pending", "dispatched")
STAGE_STATUSES: Final = ("pending", "running", "succeeded", "failed", "cancelled", "skipped")


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    jobs: dict[tuple[str, str], int]
    outbox: dict[tuple[str, str], int]
    stages: dict[tuple[str, str], int]
    stage_duration_sum_seconds: dict[str, float]
    stage_duration_samples: dict[str, int]
    oldest_queued_job_seconds: float
    due_outbox: int
    retrying_outbox: int

    def render(self, *, redis_queue_depths: dict[str, int]) -> str:
        """Render valid Prometheus text exposition 0.0.4 with closed label sets."""
        queues = ("ingest", "generation", "analysis", "export", "legacy")
        if set(redis_queue_depths) != set(queues):
            raise ValueError("Redis metric queue set must match five canonical queues")
        if any(type(value) is not int or value < 0 for value in redis_queue_depths.values()):
            raise ValueError("Redis queue depth must be a non-negative integer")

        lines: list[str] = []

        def gauge(name: str, help_text: str, values: list[tuple[str, float | int]]) -> None:
            lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
            for label, value in values:
                if not (isinstance(value, int | float) and value >= 0):
                    raise ValueError("Prometheus metrics must be non-negative")
                lines.append(f"{name}{label} {value}")

        gauge(
            "udg_jobs",
            "Authoritative job rows by allowlisted type and status.",
            [
                (f'{{type="{job_type}",status="{status}"}}',
                 self.jobs.get((job_type, status), 0))
                for job_type in JOB_TYPES for status in JOB_STATUSES
            ],
        )
        gauge(
            "udg_outbox_messages",
            "Authoritative outbox messages by allowlisted logical queue and status.",
            [
                (f'{{queue="{queue}",status="{status}"}}',
                 self.outbox.get((queue, status), 0))
                for queue in OUTBOX_QUEUES for status in OUTBOX_STATUSES
            ],
        )
        gauge(
            "udg_stages",
            "Persisted canonical stage rows by status.",
            [
                (f'{{stage="{stage}",status="{status}"}}',
                 self.stages.get((stage, status), 0))
                for stage in CANONICAL_STAGE_ORDER for status in STAGE_STATUSES
            ],
        )
        gauge(
            "udg_stage_duration_seconds_sum",
            "Cumulative recorded terminal stage duration seconds, mutable DB snapshot.",
            [
                (f'{{stage="{stage}"}}', self.stage_duration_sum_seconds.get(stage, 0.0))
                for stage in CANONICAL_STAGE_ORDER
            ],
        )
        gauge(
            "udg_stage_duration_samples",
            "Number of terminal stages contributing recorded durations.",
            [
                (f'{{stage="{stage}"}}', self.stage_duration_samples.get(stage, 0))
                for stage in CANONICAL_STAGE_ORDER
            ],
        )
        gauge(
            "udg_arq_queue_depth",
            "ARQ Redis sorted-set queued job count, not running workers.",
            [(f'{{queue="{queue}"}}', redis_queue_depths[queue]) for queue in queues],
        )
        gauge(
            "udg_oldest_queued_job_age_seconds",
            "Age of oldest queued authoritative DB job, or zero when empty.",
            [("", self.oldest_queued_job_seconds)],
        )
        gauge(
            "udg_outbox_due",
            "Pending outbox messages currently eligible for dispatch.",
            [("", self.due_outbox)],
        )
        gauge(
            "udg_outbox_retries",
            "Pending outbox messages with at least one delivery attempt.",
            [("", self.retrying_outbox)],
        )
        return "\n".join(lines) + "\n"


def read_operational_snapshot(session: Session) -> OperationalSnapshot:
    """Query aggregated counts only. Never materialize full Job or Stage rows."""
    jobs = {
        (str(job_type), str(status)): int(count)
        for job_type, status, count in session.execute(
            select(Job.job_type, Job.status, func.count())
            .where(Job.job_type.in_(JOB_TYPES), Job.status.in_(JOB_STATUSES))
            .group_by(Job.job_type, Job.status)
        )
    }
    outbox = {
        (str(queue), str(status)): int(count)
        for queue, status, count in session.execute(
            select(JobOutbox.queue_name, JobOutbox.status, func.count())
            .where(
                JobOutbox.queue_name.in_(OUTBOX_QUEUES),
                JobOutbox.status.in_(OUTBOX_STATUSES),
            )
            .group_by(JobOutbox.queue_name, JobOutbox.status)
        )
    }
    stages = {
        (str(stage), str(status)): int(count)
        for stage, status, count in session.execute(
            select(RunStageResult.stage_name, RunStageResult.status, func.count())
            .where(
                RunStageResult.stage_name.in_(CANONICAL_STAGE_ORDER),
                RunStageResult.status.in_(STAGE_STATUSES),
            )
            .group_by(RunStageResult.stage_name, RunStageResult.status)
        )
    }
    durations = session.execute(
        select(
            RunStageResult.stage_name,
            func.count(),
            func.sum(
                func.extract(
                    "epoch", RunStageResult.finished_at - RunStageResult.started_at
                )
            ),
        )
        .where(
            RunStageResult.stage_name.in_(CANONICAL_STAGE_ORDER),
            RunStageResult.status.in_(("succeeded", "failed", "cancelled", "skipped")),
            RunStageResult.started_at.is_not(None),
            RunStageResult.finished_at.is_not(None),
            RunStageResult.finished_at >= RunStageResult.started_at,
        )
        .group_by(RunStageResult.stage_name)
    ).all()
    oldest_age = session.scalar(
        select(func.max(func.extract("epoch", func.now() - Job.created_at)))
        .where(Job.status == "queued")
    )
    due = session.scalar(
        select(func.count())
        .select_from(JobOutbox)
        .where(
            JobOutbox.status == "pending",
            (JobOutbox.next_attempt_at.is_(None))
            | (JobOutbox.next_attempt_at <= func.now()),
        )
    )
    retrying = session.scalar(
        select(func.count())
        .select_from(JobOutbox)
        .where(JobOutbox.status == "pending", JobOutbox.delivery_attempts > 0)
    )
    return OperationalSnapshot(
        jobs=jobs,
        outbox=outbox,
        stages=stages,
        stage_duration_sum_seconds={
            str(stage): max(0.0, float(total)) for stage, _, total in durations
        },
        stage_duration_samples={str(stage): int(count) for stage, count, _ in durations},
        oldest_queued_job_seconds=max(0.0, float(oldest_age or 0.0)),
        due_outbox=int(due or 0),
        retrying_outbox=int(retrying or 0),
    )
