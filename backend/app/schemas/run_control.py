"""Transport-only run creation/control and DB-authoritative progress projections."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SEED = (1 << 63) - 1


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["EXPANSION", "FROM_SCRATCH"] = "EXPANSION"
    seed: int = Field(ge=0, le=MAX_SEED)
    dataset_version_ids: list[uuid.UUID] = Field(min_length=1, max_length=32)
    config_json: dict[str, Any] = Field(default_factory=dict)
    config_schema_version: str = Field(min_length=1, max_length=64)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")

    @field_validator("dataset_version_ids")
    @classmethod
    def distinct_versions(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(values)) != len(values):
            raise ValueError("dataset_version_ids must be distinct")
        return values

    @field_validator("config_schema_version")
    @classmethod
    def nonempty_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("config_schema_version must be non-empty")
        return value


class RunJobRead(BaseModel):
    id: uuid.UUID
    status: str
    attempt_count: int
    max_attempts: int
    cancel_requested_at: datetime | None
    error_class: str | None
    error_code: str | None


class RunStageRead(BaseModel):
    stage_name: str
    stage_version: str
    status: str
    progress_percent: int
    started_at: datetime | None
    finished_at: datetime | None


class RunStateRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    rerun_source_id: uuid.UUID | None
    status: str
    mode: str
    seed: int
    job: RunJobRead | None
    stages: list[RunStageRead]
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunListRead(BaseModel):
    project_id: uuid.UUID
    limit: int
    truncated: bool
    runs: list[RunStateRead]
