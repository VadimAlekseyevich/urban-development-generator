import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
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
        CheckConstraint(
            "zone_class IN ('residential', 'mixed', 'public', 'recreation')",
            name="ck_generated_zones_zone_class",
        ),
        CheckConstraint(
            "area_m2 > 0",
            name="ck_generated_zones_area_positive",
        ),
        CheckConstraint(
            "jsonb_typeof(diagnostics_json) = 'object'",
            name="ck_generated_zones_diagnostics_object",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )
    zone_class: Mapped[str] = mapped_column(String(32), nullable=False)
    area_m2: Mapped[float] = mapped_column(Float, nullable=False)
    diagnostics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
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
        Index(
            "uq_generated_blocks_run_block_key",
            "run_id",
            "block_key",
            unique=True,
        ),
        Index("ix_generated_blocks_run_zone_id", "run_id", "zone_id"),
        Index(
            "ix_generated_blocks_run_association_status",
            "run_id",
            "association_status",
        ),
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_generated_blocks_attributes_object",
        ),
        CheckConstraint(
            "block_key IS NULL OR length(btrim(block_key)) > 0",
            name="ck_generated_blocks_block_key_nonempty",
        ),
        CheckConstraint(
            "area_m2 IS NULL OR area_m2 > 0",
            name="ck_generated_blocks_area_positive",
        ),
        CheckConstraint(
            "association_status IS NULL OR association_status IN "
            "('ASSOCIATED', 'NO_OVERLAP', 'PARTIAL_OVERLAP', "
            "'AMBIGUOUS_FULL_COVERAGE')",
            name="ck_generated_blocks_association_status",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )
    block_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_zones.id", ondelete="SET NULL"),
        nullable=True,
    )
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    association_status: Mapped[str | None] = mapped_column(String(32), nullable=True)


class GeneratedParcel(GeneratedEntityMixin, Base):
    __tablename__ = "generated_parcels"
    __table_args__ = (
        *_generated_access_indexes("generated_parcels"),
        Index(
            "uq_generated_parcels_run_parcel_key",
            "run_id",
            "parcel_key",
            unique=True,
        ),
        Index("ix_generated_parcels_run_block_id", "run_id", "block_id"),
        Index("ix_generated_parcels_run_zone_id", "run_id", "zone_id"),
        Index(
            "ix_generated_parcels_buildable_geometry",
            "buildable_geometry",
            postgresql_using="gist",
        ),
        CheckConstraint(
            "jsonb_typeof(attributes_json) = 'object'",
            name="ck_generated_parcels_attributes_object",
        ),
        CheckConstraint(
            "parcel_key IS NULL OR length(btrim(parcel_key)) > 0",
            name="ck_generated_parcels_parcel_key_nonempty",
        ),
        CheckConstraint(
            "area_m2 IS NULL OR area_m2 > 0",
            name="ck_generated_parcels_area_positive",
        ),
        CheckConstraint(
            "buildable_area_m2 IS NULL OR buildable_area_m2 > 0",
            name="ck_generated_parcels_buildable_area_positive",
        ),
        CheckConstraint(
            "frontage_m IS NULL OR frontage_m >= 0",
            name="ck_generated_parcels_frontage_nonnegative",
        ),
    )

    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=-1, spatial_index=False),
        nullable=False,
    )
    parcel_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    block_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_blocks.id", ondelete="CASCADE"),
        nullable=True,
    )
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_zones.id", ondelete="SET NULL"),
        nullable=True,
    )
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    buildable_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    frontage_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    buildable_geometry: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=-1, spatial_index=False),
        nullable=True,
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
