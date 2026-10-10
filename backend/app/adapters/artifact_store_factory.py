from functools import lru_cache

from backend.app.adapters.local_artifact_store import LocalArtifactStore
from backend.app.adapters.s3_artifact_store import S3ArtifactStore
from backend.app.core.config import Settings, settings

ArtifactStoreAdapter = LocalArtifactStore | S3ArtifactStore


def build_artifact_store(config: Settings) -> ArtifactStoreAdapter:
    """Build the configured blob adapter without changing the core ArtifactStore port."""
    if config.artifact_store_backend == "local":
        return LocalArtifactStore(config.storage_root)

    assert config.s3_endpoint_url is not None
    assert config.s3_bucket is not None
    assert config.s3_access_key is not None
    assert config.s3_secret_key is not None
    return S3ArtifactStore(
        endpoint_url=config.s3_endpoint_url,
        bucket=config.s3_bucket,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
        region=config.s3_region,
        prefix=config.s3_prefix,
        force_path_style=config.s3_force_path_style,
        session_token=config.s3_session_token,
        verify_tls=config.s3_verify_tls,
    )


@lru_cache(maxsize=1)
def get_runtime_artifact_store() -> ArtifactStoreAdapter:
    """Reuse one process-local adapter/client and its bounded orphan-scan cursor."""
    return build_artifact_store(settings)
