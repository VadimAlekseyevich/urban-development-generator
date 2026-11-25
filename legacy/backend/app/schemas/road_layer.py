import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RoadRunSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int = Field(gt=0)
    generated_road_count: int = Field(ge=0)
    created_at: datetime
    finished_at: datetime | None


class GeneratedRoadGeoJSONFeature(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["Feature"]
    id: uuid.UUID
    geometry: dict[str, Any]
    properties: dict[str, Any]


class GeneratedRoadGeoJSONResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["FeatureCollection"]
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int = Field(gt=0)
    limit: int = Field(ge=1)
    truncated: bool
    features: list[GeneratedRoadGeoJSONFeature]


class RoadGraphDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    edge_count: int = Field(ge=0)
    road_count: int = Field(ge=0)
    node_count: int = Field(ge=0)
    component_count: int = Field(ge=0)
    dead_end_node_count: int = Field(ge=0)
    dead_end_ratio: float = Field(ge=0.0, le=1.0)
    total_length_m: float = Field(ge=0.0)
    class_counts: dict[str, int]
    origin_counts: dict[str, int]
