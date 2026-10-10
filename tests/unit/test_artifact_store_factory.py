from pathlib import Path

import pytest

from backend.app.adapters.artifact_store_factory import build_artifact_store
from backend.app.adapters.local_artifact_store import LocalArtifactStore
from backend.app.adapters.s3_artifact_store import S3ArtifactStore
from backend.app.core.config import Settings


def test_artifact_store_factory_defaults_to_local(tmp_path: Path) -> None:
    config = Settings(_env_file=None, storage_root=tmp_path)

    store = build_artifact_store(config)

    assert isinstance(store, LocalArtifactStore)


def test_artifact_store_factory_builds_s3_adapter() -> None:
    config = Settings(
        _env_file=None,
        artifact_store_backend="s3",
        s3_endpoint_url="http://minio:9000",
        s3_bucket="urban-artifacts",
        s3_access_key="access",
        s3_secret_key="secret",
    )

    store = build_artifact_store(config)

    assert isinstance(store, S3ArtifactStore)


def test_s3_settings_require_endpoint_bucket_and_credentials() -> None:
    with pytest.raises(ValueError, match="S3_BUCKET"):
        Settings(
            _env_file=None,
            artifact_store_backend="s3",
            s3_endpoint_url="http://minio:9000",
            s3_access_key="access",
            s3_secret_key="secret",
        )
