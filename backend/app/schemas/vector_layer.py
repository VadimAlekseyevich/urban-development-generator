"""Pydantic boundary for S13 bounded, owner-qualified vector keyset pages."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class VectorFeatureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["Feature"]
    id: uuid.UUID
    geometry: dict[str, Any]
    properties: dict[str, Any]


class VectorPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    schema_version: Literal["bounded-vector-v1"]
    type: Literal["FeatureCollection"]
    project_id: uuid.UUID
    layer_id: str
    definition_version: str
    dataset_version_id: uuid.UUID | None
    run_id: uuid.UUID | None
    working_srid: int = Field(gt=0)
    query_bbox: tuple[float, float, float, float]
    presentation_crs: Literal["EPSG:4326"]
    simplify_m: float = Field(ge=0, le=100)
    limit: int = Field(ge=1, le=5000)
    truncated: bool
    next_after: uuid.UUID | None
    features: list[VectorFeatureResponse]
