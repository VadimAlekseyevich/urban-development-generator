from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.application.geopackage_exports import (
    DEFAULT_GEOPACKAGE_MAX_FEATURES_PER_LAYER,
    DEFAULT_GEOPACKAGE_MAX_TOTAL_FEATURES,
    MAX_GEOPACKAGE_EXPORT_LAYERS,
    MAX_GEOPACKAGE_FEATURES_PER_LAYER,
    MAX_GEOPACKAGE_TOTAL_FEATURES,
)


class GeoPackageExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_GEOPACKAGE_EXPORT_LAYERS,
    )
    dataset_version_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    bbox: tuple[float, float, float, float]
    max_features_per_layer: int = Field(
        default=DEFAULT_GEOPACKAGE_MAX_FEATURES_PER_LAYER,
        ge=1,
        le=MAX_GEOPACKAGE_FEATURES_PER_LAYER,
    )
    max_total_features: int = Field(
        default=DEFAULT_GEOPACKAGE_MAX_TOTAL_FEATURES,
        ge=1,
        le=MAX_GEOPACKAGE_TOTAL_FEATURES,
    )


class GeoPackageLayerCountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    layer_id: str
    feature_count: int = Field(ge=0)


class GeoPackageExportArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: uuid.UUID
    filename: str
    size_bytes: int = Field(ge=0)
    checksum: str
    content_type: str


class GeoPackageExportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    schema_version: Literal["geopackage-export-v1"]
    job_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(gt=0)
    layer_ids: tuple[str, ...]
    dataset_version_id: uuid.UUID | None
    run_id: uuid.UUID | None
    bbox: tuple[float, float, float, float]
    max_features_per_layer: int = Field(
        ge=1,
        le=MAX_GEOPACKAGE_FEATURES_PER_LAYER,
    )
    max_total_features: int = Field(
        ge=1,
        le=MAX_GEOPACKAGE_TOTAL_FEATURES,
    )
    layer_counts: tuple[GeoPackageLayerCountRead, ...] | None
    total_feature_count: int | None = Field(default=None, ge=0)
    artifact: GeoPackageExportArtifactRead | None
    error_class: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
