"""One bounded read of immutable, project-scoped comparison inputs."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.run_compare import CompareRunRecord
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project


class SqlAlchemyRunCompareRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return self._session.scalar(
            select(Project.id).where(Project.id == project_id)
        ) is not None

    def get_runs(
        self, *, project_id: uuid.UUID, run_ids: tuple[uuid.UUID, ...]
    ) -> list[CompareRunRecord]:
        rows = self._session.execute(
            select(
                GenerationRun.id,
                GenerationRun.project_id,
                GenerationRun.status,
                GenerationRun.mode,
                GenerationRun.seed,
                GenerationRun.working_srid,
                GenerationRun.metrics_json,
                GenerationRun.validation_json,
                GenerationRun.created_at,
                GenerationRun.finished_at,
            ).where(
                GenerationRun.project_id == project_id,
                GenerationRun.id.in_(run_ids),
            )
        ).mappings().all()
        return [_record(row) for row in rows]


def _record(row: Any) -> CompareRunRecord:
    return CompareRunRecord(
        id=row["id"],
        project_id=row["project_id"],
        status=row["status"],
        mode=row["mode"],
        seed=row["seed"],
        working_srid=row["working_srid"],
        metrics_json=row["metrics_json"],
        validation_json=row["validation_json"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )
