import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class GeoJsonExport(Base):
    """Immutable request metadata and optional published artifact for one export job."""

    __tablename__ = "geojson_exports"
    __table_args__ = (
        CheckConstraint(
            "layer_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'",
            name="ck_geojson_exports_layer_id",
        ),
        CheckConstraint(
            "(dataset_version_id IS NOT NULL) <> (run_id IS NOT NULL)",
            name="ck_geojson_exports_exact_owner",
        ),
        CheckConstraint(
            "bbox_west >= -180 AND bbox_west < bbox_east AND bbox_east <= 180",
            name="ck_geojson_exports_longitude_bounds",
        ),
        CheckConstraint(
            "bbox_south >= -90 AND bbox_south < bbox_north AND bbox_north <= 90",
            name="ck_geojson_exports_latitude_bounds",
        ),
        CheckConstraint(
            "max_features > 0 AND max_features <= 100000",
            name="ck_geojson_exports_max_features",
        ),
        UniqueConstraint("artifact_id", name="uq_geojson_exports_artifact_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    layer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    bbox_west: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_south: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_east: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_north: Mapped[float] = mapped_column(Float, nullable=False)
    max_features: Mapped[int] = mapped_column(Integer, nullable=False, default=100000)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="RESTRICT"),
        nullable=True,
    )

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            self.bbox_west,
            self.bbox_south,
            self.bbox_east,
            self.bbox_north,
        )
