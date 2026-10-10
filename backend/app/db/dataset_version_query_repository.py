"""Bounded SQL read model; no creation or mutation of dataset versions."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.dataset_versions import (
    DatasetVersionPage,
    DatasetVersionSummary,
)
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project


class SqlAlchemyDatasetVersionReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_versions(
        self, *, project_id: uuid.UUID, limit: int, offset: int
    ) -> DatasetVersionPage | None:
        if self._session.get(Project, project_id) is None:
            return None
        statement = (
            select(DatasetVersion, Dataset.kind)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(Dataset.project_id == project_id)
            .order_by(DatasetVersion.created_at.desc(), DatasetVersion.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
        rows = self._session.execute(statement).all()
        return DatasetVersionPage(
            project_id=project_id,
            limit=limit,
            offset=offset,
            truncated=len(rows) > limit,
            versions=tuple(
                DatasetVersionSummary(
                    id=version.id,
                    dataset_id=version.dataset_id,
                    dataset_kind=kind,
                    version=version.version,
                    status=version.status,
                    checksum_sha256=version.checksum_sha256,
                    created_at=version.created_at,
                )
                for version, kind in rows[:limit]
            ),
        )
