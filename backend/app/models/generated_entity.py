import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


def _generated_access_indexes(table_name: str) -> tuple[Index, Index]:
    """Indexes for run-scoped keyset and bbox access."""

    return (
        Index(f"ix_{table_name}_run_id_id", "run_id", "id"),
        Index(
            f"ix_{table_name}_geometry",
            "geometry",
            postgresql_using="gist",
        ),
    )


class GeneratedEntityMixin:
    """Columns shared by all run-scoped generated spatial entities."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attributes_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GeneratedZone(GeneratedEntityMixin, Base):
    __tablename__ = "generated_zones"
    __table_args__ = (
        *_generated_access_indexes("generated_zones"),
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_generated_zones_attributes_object",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )


class GeneratedRoad(GeneratedEntityMixin, Base):
    __tablename__ = "generated_roads"
    __table_args__ = (
        *_generated_access_indexes("generated_roads"),
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_generated_roads_attributes_object",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=-1, spatial_index=False),
        nullable=False,
    )


class GeneratedBlock(GeneratedEntityMixin, Base):
    __tablename__ = "generated_blocks"
    __table_args__ = (
        *_generated_access_indexes("generated_blocks"),
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_generated_blocks_attributes_object",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )


class GeneratedParcel(GeneratedEntityMixin, Base):
    __tablename__ = "generated_parcels"
    __table_args__ = (
        *_generated_access_indexes("generated_parcels"),
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_generated_parcels_attributes_object",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )


class GeneratedBuilding(GeneratedEntityMixin, Base):
    __tablename__ = "generated_buildings"
    __table_args__ = (
        *_generated_access_indexes("generated_buildings"),
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_generated_buildings_attributes_object",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )


class GeneratedInfrastructure(GeneratedEntityMixin, Base):
    __tablename__ = "generated_infrastructure"
    __table_args__ = (
        *_generated_access_indexes("generated_infrastructure"),
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_generated_infrastructure_attributes_object",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=-1, spatial_index=False),
        nullable=False,
    )
