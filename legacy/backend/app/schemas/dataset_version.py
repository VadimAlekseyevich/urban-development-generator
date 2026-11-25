"""Dataset version catalog response without conflating uploads and versions."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DatasetVersionSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_kind: str
    version: int = Field(ge=1)
    status: str
    checksum_sha256: str | None
    created_at: datetime


class DatasetVersionPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    limit: int
    offset: int
    truncated: bool
    versions: list[DatasetVersionSummaryRead]
