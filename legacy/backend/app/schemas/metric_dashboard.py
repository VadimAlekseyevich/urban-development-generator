import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from core.urban_generator.domain.benchmarking import (
    MetricDirection,
    MetricScope,
    RawMetricId,
)


class MetricRunSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int = Field(gt=0)
    composite_score: float = Field(ge=0.0, le=1.0)
    metric_count: int = Field(ge=1)
    score_config_id: str
    score_config_version: str
    normalization_profile_id: str
    normalization_profile_version: str
    created_at: datetime
    finished_at: datetime | None


class MetricRunListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    limit: int = Field(ge=1)
    truncated: bool
    runs: list[MetricRunSummaryResponse]


class MetricDashboardMetricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric_id: RawMetricId
    unit: str
    scope: MetricScope
    direction: MetricDirection
    metric_version: str
    raw_value: float | None
    normalized_value: float | None = Field(default=None, ge=0.0, le=1.0)
    normalization_policy_version: str
    configured_weight: float = Field(ge=0.0)
    normalized_weight: float = Field(ge=0.0, le=1.0)
    contribution: float = Field(ge=0.0, le=1.0)
    was_clamped: bool
    was_missing: bool


class MetricDashboardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int = Field(gt=0)
    composite_score: float = Field(ge=0.0, le=1.0)
    score_config_id: str
    score_config_version: str
    normalization_profile_id: str
    normalization_profile_version: str
    metrics: list[MetricDashboardMetricResponse]
    created_at: datetime
    finished_at: datetime | None
