import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.urban_generator.domain import ConstraintScope, ConstraintSeverity


class ValidationRunSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int = Field(gt=0)
    violation_count: int = Field(ge=0)
    hard_violation_count: int = Field(ge=0)
    soft_violation_count: int = Field(ge=0)
    spatial_violation_count: int = Field(ge=0)
    created_at: datetime
    finished_at: datetime | None


class ValidationRunListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    limit: int = Field(ge=1)
    truncated: bool
    runs: list[ValidationRunSummaryResponse]


class ViolationSoftPenaltyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    raw_penalty: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0)
    weighted_penalty: float = Field(ge=0.0)
    schema_version: int = Field(ge=1)


class ViolationDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    violation_index: int = Field(ge=0)
    code: str
    severity: ConstraintSeverity
    scope: ConstraintScope
    message: str
    entity_id: str | None
    has_problem_geometry: bool
    soft_penalty: ViolationSoftPenaltyResponse | None


class ViolationListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    run_id: uuid.UUID
    working_srid: int = Field(gt=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    total: int = Field(ge=0)
    truncated: bool
    violations: list[ViolationDetailResponse]


class ViolationGeoJSONFeature(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["Feature"]
    id: str
    geometry: dict[str, Any]
    properties: dict[str, Any]


class ViolationGeoJSONResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["FeatureCollection"]
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int = Field(gt=0)
    limit: int = Field(ge=1)
    matching_count: int = Field(ge=0)
    truncated: bool
    features: list[ViolationGeoJSONFeature]
