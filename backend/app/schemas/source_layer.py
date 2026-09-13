import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.application.source_layers import SourceLayerName


class SourceLayerGeoJSONFeature(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["Feature"]
    id: uuid.UUID
    geometry: dict[str, Any]
    properties: dict[str, Any]


class SourceLayerGeoJSONResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["FeatureCollection"]
    project_id: uuid.UUID
    dataset_version_id: uuid.UUID
    layer: SourceLayerName
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int = Field(gt=0)
    limit: int = Field(ge=1)
    truncated: bool
    features: list[SourceLayerGeoJSONFeature]
