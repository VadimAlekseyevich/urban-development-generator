"""User-submitted artifact ingestion contract; workers alone publish ready versions."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self


class DatasetIngestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: uuid.UUID
    dataset_kind: Literal[
        "roads", "buildings", "landuse", "water", "facilities", "constraints", "dem"
    ]
    source_format: Literal["geojson", "gpkg", "shapefile_zip", "geotiff"]
    layer: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("layer")
    @classmethod
    def validate_layer(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("layer must not be blank")
        return value

    @model_validator(mode="after")
    def check_source_kind(self) -> Self:
        if (self.dataset_kind == "dem") != (self.source_format == "geotiff"):
            raise ValueError("DEM requires GeoTIFF and GeoTIFF requires DEM")
        if self.layer is not None and self.source_format != "gpkg":
            raise ValueError("named layer is only accepted for GeoPackage")
        return self


class DatasetIngestRead(BaseModel):
    project_id: uuid.UUID
    dataset_version_id: uuid.UUID
    dataset_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    job_status: str
