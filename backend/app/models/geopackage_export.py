import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class GeoPackageExport(Base):
    """Immutable request metadata and optional published multi-layer artifact."""

    __tablename__ = "geopackage_exports"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(layer_ids_json) = 'array' "
            "AND jsonb_array_length(layer_ids_json) BETWEEN 1 AND 15",
            name="ck_geopackage_exports_layer_ids",
        ),
        CheckConstraint(
            "dataset_version_id IS NOT NULL OR run_id IS NOT NULL",
            name="ck_geopackage_exports_owner_present",
        ),
        CheckConstraint(
            "bbox_west >= -180 AND bbox_west < bbox_east AND bbox_east <= 180",
            name="ck_geopackage_exports_longitude_bounds",
        ),
        CheckConstraint(
            "bbox_south >= -90 AND bbox_south < bbox_north AND bbox_north <= 90",
            name="ck_geopackage_exports_latitude_bounds",
        ),
        CheckConstraint(
            "max_features_per_layer > 0 AND max_features_per_layer <= 100000",
            name="ck_geopackage_exports_per_layer_cap",
        ),
        CheckConstraint(
            "max_total_features > 0 AND max_total_features <= 500000",
            name="ck_geopackage_exports_total_cap",
        ),
        CheckConstraint(
            "layer_feature_counts_json IS NULL "
            "OR jsonb_typeof(layer_feature_counts_json) = 'object'",
            name="ck_geopackage_exports_feature_counts",
        ),
        UniqueConstraint("artifact_id", name="uq_geopackage_exports_artifact_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    layer_ids_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
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
    max_features_per_layer: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100000,
    )
    max_total_features: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=250000,
    )
    layer_feature_counts_json: Mapped[dict[str, int] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )
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
