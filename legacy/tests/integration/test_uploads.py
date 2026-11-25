import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.adapters import LocalArtifactStore
from backend.app.api.dependencies import get_upload_service
from backend.app.application.uploads import UploadService
from backend.app.main import app
from backend.app.models.artifact import Artifact
from core.urban_generator.domain import ArtifactRef, ArtifactState


class FakeArtifactRepository:
    def __init__(self) -> None:
        self.artifacts: list[Artifact] = []

    def create(self, artifact: Artifact) -> Artifact:
        artifact.id = uuid.uuid4()
        self.artifacts.append(artifact)
        return artifact


def test_streaming_upload_api_returns_ready_artifact_metadata(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, chunk_size=4)
    repository = FakeArtifactRepository()
    service = UploadService(store, repository, max_size_bytes=64)

    app.dependency_overrides[get_upload_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/uploads",
            files={
                "file": (
                    "../../roads data.geojson",
                    b'{"type":"FeatureCollection","features":[]}',
                    "application/geo+json",
                )
            },
        )
    finally:
        app.dependency_overrides.pop(get_upload_service, None)

    assert response.status_code == 201
    payload = response.json()
    assert payload["filename"] == "roads_data.geojson"
    assert payload["state"] == "ready"
    assert payload["size_bytes"] > 0
    assert payload["checksum"].startswith("sha256:")
    assert ".." not in payload["key"]
    assert repository.artifacts[0].id == uuid.UUID(payload["artifact_id"])

    ref = ArtifactRef(key=payload["key"], state=ArtifactState.READY)
    with store.open(ref) as handle:
        assert handle.read() == b'{"type":"FeatureCollection","features":[]}'


def test_streaming_upload_api_rejects_oversized_file_without_persisting(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path, chunk_size=2)
    repository = FakeArtifactRepository()
    service = UploadService(store, repository, max_size_bytes=4)

    app.dependency_overrides[get_upload_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/uploads",
            files={"file": ("too-large.bin", b"12345", "application/octet-stream")},
        )
    finally:
        app.dependency_overrides.pop(get_upload_service, None)

    assert response.status_code == 413
    assert response.json()["detail"] == "Upload exceeds the configured 4 byte limit"
    assert repository.artifacts == []
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
