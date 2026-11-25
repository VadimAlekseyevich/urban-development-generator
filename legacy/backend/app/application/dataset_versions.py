"""Read-only, project-scoped discovery of immutable dataset versions."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DatasetVersionSummary:
    id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_kind: str
    version: int
    status: str
    checksum_sha256: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DatasetVersionPage:
    project_id: uuid.UUID
    limit: int
    offset: int
    truncated: bool
    versions: tuple[DatasetVersionSummary, ...]


class DatasetVersionProjectNotFoundError(LookupError):
    """The requested project does not exist."""


class DatasetVersionReader(Protocol):
    def list_versions(
        self, *, project_id: uuid.UUID, limit: int, offset: int
    ) -> DatasetVersionPage | None: ...


class DatasetVersionQueryService:
    def __init__(self, reader: DatasetVersionReader) -> None:
        self._reader = reader

    def list_versions(
        self, *, project_id: uuid.UUID, limit: int = 20, offset: int = 0
    ) -> DatasetVersionPage:
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError("invalid dataset version page bounds")
        result = self._reader.list_versions(
            project_id=project_id, limit=limit, offset=offset
        )
        if result is None:
            raise DatasetVersionProjectNotFoundError("Project not found")
        return result
