import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DemographyAgeGroupMetricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    min_age: int = Field(ge=0)
    max_age: int | None = Field(default=None, ge=0)
    residents: int = Field(ge=0)
    share: float = Field(ge=0.0, le=1.0)


class DemographyRunSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int = Field(gt=0)
    block_count: int = Field(ge=0)
    population: int = Field(ge=0)
    population_density_per_km2: float = Field(ge=0.0)
    jobs_estimate: float = Field(ge=0.0)
    created_at: datetime
    finished_at: datetime | None


class DemographyMetricsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    run_id: uuid.UUID
    scenario_version: str
    scenario_fingerprint: str
    employment_config_version: str
    employment_config_fingerprint: str
    block_count: int = Field(ge=0)
    area_m2: float = Field(ge=0.0)
    population: int = Field(ge=0)
    population_density_per_km2: float = Field(ge=0.0)
    jobs_estimate: float = Field(ge=0.0)
    age_groups: list[DemographyAgeGroupMetricResponse]


class DemographyGeoJSONFeature(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["Feature"]
    id: uuid.UUID
    geometry: dict[str, Any]
    properties: dict[str, Any]


class DemographyGeoJSONResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["FeatureCollection"]
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int = Field(gt=0)
    limit: int = Field(ge=1)
    truncated: bool
    features: list[DemographyGeoJSONFeature]
