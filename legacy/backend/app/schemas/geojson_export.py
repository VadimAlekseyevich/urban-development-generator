from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.application.geojson_exports import (
    DEFAULT_GEOJSON_EXPORT_MAX_FEATURES,
    MAX_GEOJSON_EXPORT_FEATURES,
)


class GeoJsonExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    dataset_version_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    bbox: tuple[float, float, float, float]
    max_features: int = Field(
        default=DEFAULT_GEOJSON_EXPORT_MAX_FEATURES,
        ge=1,
        le=MAX_GEOJSON_EXPORT_FEATURES,
    )


class GeoJsonExportArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: uuid.UUID
    filename: str
    size_bytes: int = Field(ge=0)
    checksum: str
    content_type: str


class GeoJsonExportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    schema_version: Literal["geojson-export-v1"]
    job_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(gt=0)
    layer_id: str
    dataset_version_id: uuid.UUID | None
    run_id: uuid.UUID | None
    bbox: tuple[float, float, float, float]
    max_features: int = Field(ge=1, le=MAX_GEOJSON_EXPORT_FEATURES)
    feature_count: int | None = Field(default=None, ge=0)
    artifact: GeoJsonExportArtifactRead | None
    error_class: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
