import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    event,
    ForeignKey,
    func,
    inspect,
    Integer,
    String,
    text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base


IMMUTABLE_DATASET_VERSION_FIELDS = frozenset(
    {
        "id",
        "dataset_id",
        "version",
        "checksum_sha256",
        "source_metadata",
        "created_at",
    }
)


class DatasetVersionImmutableError(ValueError):
    """Raised when immutable dataset-version content is changed in place."""


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project = relationship("Project", back_populates="datasets")
    versions: Mapped[list["DatasetVersion"]] = relationship(
        "DatasetVersion",
        back_populates="dataset",
        cascade="all, delete-orphan",
        order_by="DatasetVersion.version",
    )


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_dataset_versions_version_positive"),
        CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dataset_versions_checksum_sha256",
        ),
        UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_dataset_versions_dataset_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="uploaded", index=True
    )
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    dataset: Mapped[Dataset] = relationship("Dataset", back_populates="versions")


@event.listens_for(DatasetVersion, "before_update")
def prevent_dataset_version_content_update(
    _mapper: object,
    _connection: object,
    target: DatasetVersion,
) -> None:
    """Allow lifecycle status changes but reject edits to version identity/content."""

    state = inspect(target)
    changed_fields = {
        field_name
        for field_name in IMMUTABLE_DATASET_VERSION_FIELDS
        if state.attrs[field_name].history.has_changes()
    }
    if changed_fields:
        changed = ", ".join(sorted(changed_fields))
        raise DatasetVersionImmutableError(
            f"dataset version content is immutable; changed fields: {changed}"
        )
