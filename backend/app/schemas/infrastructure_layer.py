import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class InfrastructureRunSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int = Field(gt=0)
    existing_facility_count: int = Field(ge=0)
    generated_facility_count: int = Field(ge=0)
    created_at: datetime
    finished_at: datetime | None


class InfrastructureAgeCoverageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    demographic_group: str
    population: float = Field(ge=0.0)
    covered_population: float = Field(ge=0.0)
    coverage_ratio: float = Field(ge=0.0, le=1.0)


class InfrastructureRawMetricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric_id: str
    scalar_value: float | None
    age_coverage: list[InfrastructureAgeCoverageResponse]


class InfrastructureFacilityAccessibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    origin: Literal["existing", "generated"]
    infrastructure_type_code: str
    max_network_distance_m: float = Field(gt=0.0)
    reachable_demand_count: int = Field(ge=0)
    nearest_distance_m: float | None = Field(default=None, ge=0.0)
    farthest_distance_m: float | None = Field(default=None, ge=0.0)
    facility_id: str | None
    source_ref: str | None
    source_feature_id: str | None
    candidate_id: str | None
    acceptance_index: int | None = Field(default=None, ge=0)
    capacity: float | None = Field(default=None, gt=0.0)
    network_snapshot_id: str | None


class InfrastructureMetricsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    run_id: uuid.UUID
    read_model_version: str
    scenario_version: str
    scenario_fingerprint: str
    raw_metrics: list[InfrastructureRawMetricResponse]
    diagnostics: dict[str, int]
    facility_accessibility: list[InfrastructureFacilityAccessibilityResponse]


class InfrastructureGeoJSONFeature(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["Feature"]
    id: uuid.UUID
    origin: Literal["existing", "generated"]
    geometry: dict[str, Any]
    properties: dict[str, Any]


class InfrastructureGeoJSONResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["FeatureCollection"]
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int = Field(gt=0)
    limit: int = Field(ge=1)
    truncated: bool
    features: list[InfrastructureGeoJSONFeature]


class InfrastructureDemandGeoJSONFeature(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["Feature"]
    id: uuid.UUID
    geometry: dict[str, Any]
    properties: dict[str, Any]


class InfrastructureDemandGeoJSONResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["FeatureCollection"]
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int = Field(gt=0)
    limit: int = Field(ge=1)
    truncated: bool
    features: list[InfrastructureDemandGeoJSONFeature]
