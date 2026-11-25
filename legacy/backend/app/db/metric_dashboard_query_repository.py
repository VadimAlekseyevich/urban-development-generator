from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.metric_dashboard import (
    RUN_EVALUATION_METRICS_KEY,
    MetricDashboardRunRecord,
)
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project


class SqlAlchemyMetricDashboardQueryRepository:
    """Read run-scoped persisted evaluation envelopes for presentation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return (
            self._session.scalar(
                select(Project.id).where(Project.id == project_id)
            )
            is not None
        )

    def list_metric_runs(
        self,
        *,
        project_id: uuid.UUID,
        limit: int,
    ) -> list[MetricDashboardRunRecord]:
        statement = (
            select(
                GenerationRun.id,
                GenerationRun.project_id,
                GenerationRun.status,
                GenerationRun.mode,
                GenerationRun.seed,
                GenerationRun.working_srid,
                GenerationRun.metrics_json,
                GenerationRun.created_at,
                GenerationRun.finished_at,
            )
            .where(
                GenerationRun.project_id == project_id,
                GenerationRun.metrics_json.is_not(None),
                GenerationRun.metrics_json.op("?")(
                    RUN_EVALUATION_METRICS_KEY
                ),
            )
            .order_by(
                GenerationRun.created_at.desc(),
                GenerationRun.id.desc(),
            )
            .limit(limit)
        )
        rows = self._session.execute(statement).mappings().all()
        return [_record(row) for row in rows]

    def get_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> MetricDashboardRunRecord | None:
        statement = select(
            GenerationRun.id,
            GenerationRun.project_id,
            GenerationRun.status,
            GenerationRun.mode,
            GenerationRun.seed,
            GenerationRun.working_srid,
            GenerationRun.metrics_json,
            GenerationRun.created_at,
            GenerationRun.finished_at,
        ).where(
            GenerationRun.id == run_id,
            GenerationRun.project_id == project_id,
        )
        row = self._session.execute(statement).mappings().one_or_none()
        return _record(row) if row is not None else None


def _record(row: Any) -> MetricDashboardRunRecord:
    metrics_json = row["metrics_json"]
    if metrics_json is not None and not isinstance(metrics_json, dict):
        raise ValueError("persisted metrics_json must be an object or null")
    return MetricDashboardRunRecord(
        id=row["id"],
        project_id=row["project_id"],
        status=row["status"],
        mode=row["mode"],
        seed=row["seed"],
        working_srid=row["working_srid"],
        metrics_json=(
            cast(dict[str, Any], metrics_json)
            if metrics_json is not None
            else None
        ),
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )
