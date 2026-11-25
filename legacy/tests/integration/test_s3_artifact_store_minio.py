import os
import uuid
from io import BytesIO

import pytest

from backend.app.adapters.s3_artifact_store import S3ArtifactStore
from core.urban_generator.domain import ArtifactRef

_REQUIRED_ENV = (
    "MINIO_TEST_ENDPOINT_URL",
    "MINIO_TEST_BUCKET",
    "MINIO_TEST_ACCESS_KEY",
    "MINIO_TEST_SECRET_KEY",
)
pytestmark = pytest.mark.skipif(
    any(not os.getenv(name) for name in _REQUIRED_ENV),
    reason="MinIO smoke environment is not configured",
)


def test_s3_artifact_store_contract_against_minio() -> None:
    store = S3ArtifactStore(
        endpoint_url=os.environ["MINIO_TEST_ENDPOINT_URL"],
        bucket=os.environ["MINIO_TEST_BUCKET"],
        access_key=os.environ["MINIO_TEST_ACCESS_KEY"],
        secret_key=os.environ["MINIO_TEST_SECRET_KEY"],
        region="us-east-1",
        prefix=f"ci/{uuid.uuid4().hex}",
        force_path_style=True,
    )
    ref = ArtifactRef(
        "runs/00000000-0000-0000-0000-000000000001/stages/root/result.bin"
    )
    payload = b"minio-contract-smoke"

    temporary = store.put(
        ref,
        BytesIO(payload),
        content_type="application/octet-stream",
    )
    assert store.stat(ref) == temporary
    assert store.open(ref).read() == payload

    ready = store.promote(ref)
    assert store.stat(ready.ref) == ready
    assert store.open(ready.ref).read() == payload
    assert store.promote(ref) == ready

    store.delete(ready.ref)
    with pytest.raises(KeyError):
        store.stat(ready.ref)
