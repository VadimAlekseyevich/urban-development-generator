"""Transport-only models for persisted scenario comparison."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from core.urban_generator.domain.benchmarking import (
    MetricDirection,
    MetricScope,
    RawMetricId,
)


class RunCompareRequest(BaseModel):
    run_ids: list[uuid.UUID] = Field(min_length=2, max_length=10)


class CompareValidationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    violation_count: int = Field(ge=0)
    hard_violation_count: int = Field(ge=0)
    soft_violation_count: int = Field(ge=0)
    spatial_violation_count: int = Field(ge=0)


class CompareRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    seed: int
    mode: str
    working_srid: int = Field(gt=0)
    score_config_id: str
    score_config_version: str
    normalization_profile_id: str
    normalization_profile_version: str
    composite_score: float = Field(ge=0.0, le=1.0)
    score_delta_from_baseline: float | None
    score_rank: int | None = Field(default=None, ge=1)
    validation: CompareValidationSummaryResponse


class CompareMetricValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    raw_value: float | None
    delta_from_baseline: float | None
    rank: int | None = Field(default=None, ge=1)


class CompareMetricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric_id: RawMetricId
    unit: str
    scope: MetricScope
    direction: MetricDirection
    definition_version: str
    values: list[CompareMetricValueResponse]


class RunCompareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    baseline_run_id: uuid.UUID
    run_ids: list[uuid.UUID]
    scores_comparable: bool
    runs: list[CompareRunResponse]
    metrics: list[CompareMetricResponse]
