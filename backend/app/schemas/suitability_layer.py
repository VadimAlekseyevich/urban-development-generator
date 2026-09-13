import uuid

from pydantic import BaseModel, ConfigDict, Field


class SuitabilityFactorMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    version: str
    weight: float = Field(gt=0)
    normalization: str
    raw_min: float | None
    raw_max: float | None


class SuitabilityLayerStatisticsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_cells: int = Field(ge=0)
    valid_cells: int = Field(ge=0)
    hard_excluded_cells: int = Field(ge=0)
    invalid_data_cells: int = Field(ge=0)
    minimum_score_threshold: float = Field(ge=0, le=1)
    preferred_score_threshold: float | None = Field(default=None, ge=0, le=1)
    meets_minimum_cells: int = Field(ge=0)
    preferred_cells: int | None = Field(default=None, ge=0)
    min_score: float | None = Field(default=None, ge=0, le=1)
    max_score: float | None = Field(default=None, ge=0, le=1)
    mean_score: float | None = Field(default=None, ge=0, le=1)
    p05_score: float | None = Field(default=None, ge=0, le=1)
    p50_score: float | None = Field(default=None, ge=0, le=1)
    p95_score: float | None = Field(default=None, ge=0, le=1)


class SuitabilityLayerMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: uuid.UUID
    checksum: str
    size_bytes: int = Field(ge=0)
    content_type: str | None
    schema_version: str
    config_version: str
    config_fingerprint: str
    working_srid: int = Field(gt=0)
    working_bounds: tuple[float, float, float, float]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    image_coordinates_wgs84: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]
    wgs84_bounds: tuple[float, float, float, float]
    statistics: SuitabilityLayerStatisticsResponse
    factors: tuple[SuitabilityFactorMetadataResponse, ...]
    hard_exclusion_source_codes: tuple[str, ...]
