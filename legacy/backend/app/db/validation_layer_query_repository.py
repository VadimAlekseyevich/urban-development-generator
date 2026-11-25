from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.validation_layers import ValidationRunRecord
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project


class SqlAlchemyValidationLayerQueryRepository:
    """Read canonical validation payloads scoped to project/run identity."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return (
            self._session.scalar(
                select(Project.id).where(Project.id == project_id)
            )
            is not None
        )

    def list_validation_runs(
        self,
        *,
        project_id: uuid.UUID,
        limit: int,
    ) -> list[ValidationRunRecord]:
        statement = (
            select(
                GenerationRun.id,
                GenerationRun.project_id,
                GenerationRun.status,
                GenerationRun.mode,
                GenerationRun.seed,
                GenerationRun.working_srid,
                GenerationRun.validation_json,
                GenerationRun.created_at,
                GenerationRun.finished_at,
            )
            .where(
                GenerationRun.project_id == project_id,
                GenerationRun.validation_json.is_not(None),
            )
            .order_by(
                GenerationRun.created_at.desc(),
                GenerationRun.id.desc(),
            )
            .limit(limit)
        )
        rows = self._session.execute(statement).mappings().all()
        return [_record(row) for row in rows]

    def get_validation_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> ValidationRunRecord | None:
        statement = select(
            GenerationRun.id,
            GenerationRun.project_id,
            GenerationRun.status,
            GenerationRun.mode,
            GenerationRun.seed,
            GenerationRun.working_srid,
            GenerationRun.validation_json,
            GenerationRun.created_at,
            GenerationRun.finished_at,
        ).where(
            GenerationRun.id == run_id,
            GenerationRun.project_id == project_id,
            GenerationRun.validation_json.is_not(None),
        )
        row = self._session.execute(statement).mappings().one_or_none()
        return _record(row) if row is not None else None


def _record(row: Any) -> ValidationRunRecord:
    validation = row["validation_json"]
    if not isinstance(validation, dict):
        raise ValueError("persisted validation_json must be an object")
    return ValidationRunRecord(
        id=row["id"],
        project_id=row["project_id"],
        status=row["status"],
        mode=row["mode"],
        seed=row["seed"],
        working_srid=row["working_srid"],
        validation_json=cast(dict[str, Any], validation),
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )
