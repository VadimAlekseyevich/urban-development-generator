import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.urban_generator.domain import require_working_crs


class BoundaryMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_srid: int | None = Field(default=None, gt=0)
    geometry_type: Literal["MULTIPOLYGON"] | None = None
    feature_count: int | None = Field(default=None, ge=0)


class ProjectBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    working_srid: int = Field(gt=0)
    boundary_metadata: BoundaryMetadata = Field(default_factory=BoundaryMetadata)

    @field_validator("working_srid")
    @classmethod
    def validate_working_srid(cls, value: int) -> int:
        require_working_crs(value)
        return value


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    working_srid: int | None = Field(default=None, gt=0)
    boundary_metadata: BoundaryMetadata | None = None

    @field_validator("name")
    @classmethod
    def reject_null_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("name cannot be null")
        return value

    @field_validator("working_srid")
    @classmethod
    def validate_optional_working_srid(cls, value: int | None) -> int | None:
        if value is None:
            raise ValueError("working_srid cannot be null")
        require_working_crs(value)
        return value

    @field_validator("boundary_metadata")
    @classmethod
    def reject_null_boundary_metadata(
        cls, value: BoundaryMetadata | None
    ) -> BoundaryMetadata | None:
        if value is None:
            raise ValueError("boundary_metadata cannot be null")
        return value


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
