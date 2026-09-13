import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class SourceEntityMixin:
    """Columns shared by normalized dataset-version-scoped source features."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_feature_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceRoad(SourceEntityMixin, Base):
    __tablename__ = "source_roads"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_roads_attributes_object",
        ),
        CheckConstraint("lanes IS NULL OR lanes > 0", name="ck_source_roads_lanes_positive"),
        CheckConstraint(
            "max_speed_kph IS NULL OR max_speed_kph > 0",
            name="ck_source_roads_max_speed_positive",
        ),
        UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_roads_version_feature",
        ),
    )

    road_class: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lanes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    one_way: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTILINESTRING", srid=-1, spatial_index=False),
        nullable=False,
    )


class SourceBuilding(SourceEntityMixin, Base):
    __tablename__ = "source_buildings"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_buildings_attributes_object",
        ),
        CheckConstraint(
            "levels IS NULL OR levels > 0", name="ck_source_buildings_levels_positive"
        ),
        CheckConstraint(
            "height_m IS NULL OR height_m > 0",
            name="ck_source_buildings_height_positive",
        ),
        UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_buildings_version_feature",
        ),
    )

    building_class: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    levels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )


class SourceLanduse(SourceEntityMixin, Base):
    __tablename__ = "source_landuse"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_landuse_attributes_object",
        ),
        UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_landuse_version_feature",
        ),
    )

    landuse_class: Mapped[str] = mapped_column(String(64), nullable=False)
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )


class SourceWater(SourceEntityMixin, Base):
    __tablename__ = "source_water"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_water_attributes_object",
        ),
        UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_water_version_feature",
        ),
    )

    water_class: Mapped[str] = mapped_column(String(64), nullable=False)
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=-1, spatial_index=False),
        nullable=False,
    )


class SourceFacility(SourceEntityMixin, Base):
    __tablename__ = "source_facilities"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_facilities_attributes_object",
        ),
        CheckConstraint(
            "capacity IS NULL OR capacity >= 0",
            name="ck_source_facilities_capacity_nonnegative",
        ),
        UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_facilities_version_feature",
        ),
    )

    facility_class: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    capacity: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=-1, spatial_index=False),
        nullable=False,
    )


class SourceConstraint(SourceEntityMixin, Base):
    __tablename__ = "source_constraints"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_source_constraints_attributes_object",
        ),
        CheckConstraint(
            "severity IN ('HARD', 'SOFT')",
            name="ck_source_constraints_severity",
        ),
        CheckConstraint(
            "scope IN ('TERRITORY', 'ZONE', 'ROAD', 'BLOCK', 'PARCEL', "
            "'BUILDING', 'DEMOGRAPHY', 'INFRASTRUCTURE')",
            name="ck_source_constraints_scope",
        ),
        UniqueConstraint(
            "dataset_version_id",
            "source_feature_id",
            name="uq_source_constraints_version_feature",
        ),
    )

    constraint_code: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=-1, spatial_index=False),
        nullable=False,
    )
