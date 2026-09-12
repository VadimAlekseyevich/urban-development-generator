import uuid

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm.attributes import set_committed_value

from backend.app.models.dataset import (
    IMMUTABLE_DATASET_VERSION_FIELDS,
    Dataset,
    DatasetVersion,
    DatasetVersionImmutableError,
    prevent_dataset_version_content_update,
)


def _mark_committed(version: DatasetVersion) -> None:
    for field_name in IMMUTABLE_DATASET_VERSION_FIELDS | {"status"}:
        set_committed_value(version, field_name, getattr(version, field_name))


def _make_version() -> DatasetVersion:
    return DatasetVersion(
        id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        version=1,
        checksum_sha256="a" * 64,
        status="uploaded",
        source_metadata={"source": "roads.geojson"},
    )


def test_dataset_is_logical_record_without_upload_state() -> None:
    columns = Dataset.__table__.c

    assert {"id", "project_id", "kind", "created_at"} <= set(columns.keys())
    for legacy_column in {
        "source",
        "original_crs",
        "working_crs",
        "status",
        "storage_path",
        "metadata_json",
    }:
        assert legacy_column not in columns


def test_dataset_version_has_versioned_source_contract() -> None:
    columns = DatasetVersion.__table__.c

    assert columns.dataset_id.nullable is False
    assert columns.version.nullable is False
    assert columns.checksum_sha256.nullable is True
    assert columns.status.nullable is False
    assert columns.source_metadata.nullable is False
    assert "updated_at" not in columns

    unique_constraints = {
        constraint.name
        for constraint in DatasetVersion.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_dataset_versions_dataset_version" in unique_constraints


def test_dataset_version_content_is_immutable_but_status_can_change() -> None:
    version = _make_version()
    _mark_committed(version)

    version.status = "ready"
    prevent_dataset_version_content_update(object(), object(), version)

    _mark_committed(version)
    version.checksum_sha256 = "b" * 64

    with pytest.raises(DatasetVersionImmutableError, match="checksum_sha256"):
        prevent_dataset_version_content_update(object(), object(), version)


def test_immutable_field_set_excludes_lifecycle_status() -> None:
    assert "status" not in IMMUTABLE_DATASET_VERSION_FIELDS
    assert {
        "id",
        "dataset_id",
        "version",
        "checksum_sha256",
        "source_metadata",
        "created_at",
    } == set(IMMUTABLE_DATASET_VERSION_FIELDS)
