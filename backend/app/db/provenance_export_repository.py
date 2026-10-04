"""Persistence adapter for project-scoped provenance-export authorization."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.provenance_exports import ProvenanceExportRunRecord
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project


class SqlAlchemyProvenanceExportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return self._session.scalar(
            select(Project.id).where(Project.id == project_id)
        ) is not None

    def get_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> ProvenanceExportRunRecord | None:
        row = self._session.execute(
            select(GenerationRun.id, GenerationRun.status).where(
                GenerationRun.project_id == project_id,
                GenerationRun.id == run_id,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return ProvenanceExportRunRecord(id=row["id"], status=row["status"])
