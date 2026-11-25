import hashlib
import uuid
from io import BytesIO
from pathlib import Path

import pytest

from backend.app.adapters import LocalArtifactStore
from backend.app.application.uploads import (
    UploadService,
    UploadTooLargeError,
    sanitize_upload_filename,
)
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from core.urban_generator.domain import ArtifactRef, ArtifactState


class FakeArtifactRepository:
    def __init__(self) -> None:
        self.artifacts: list[Artifact] = []

    def create(self, artifact: Artifact) -> Artifact:
        artifact.id = uuid.uuid4()
        self.artifacts.append(artifact)
        return artifact


class FailingArtifactRepository(FakeArtifactRepository):
    def create(self, artifact: Artifact) -> Artifact:
        raise RuntimeError("database unavailable")


class GuardedSource:
    def __init__(self, payload: bytes) -> None:
        self._stream = BytesIO(payload)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert size > 0
        self.read_sizes.append(size)
        return self._stream.read(size)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../roads data (final).geojson", "roads_data_final.geojson"),
        (r"C:\fakepath\roads.geojson", "roads.geojson"),
        (".env", "env"),
        ("CON", "_CON"),
        ("", "upload.bin"),
        ("дороги 2026.geojson", "дороги_2026.geojson"),
    ],
)
def test_sanitize_upload_filename_returns_safe_leaf(raw: str, expected: str) -> None:
    assert sanitize_upload_filename(raw) == expected


def test_sanitize_upload_filename_bounds_length_and_keeps_short_suffix() -> None:
    sanitized = sanitize_upload_filename("a" * 300 + ".geojson")

    assert len(sanitized) == 180
    assert sanitized.endswith(".geojson")


def test_upload_service_streams_promotes_and_persists_metadata(tmp_path: Path) -> None:
    payload = b"road-data-" * 7
    source = GuardedSource(payload)
    store = LocalArtifactStore(tmp_path, chunk_size=5)
    repository = FakeArtifactRepository()
    service = UploadService(store, repository, max_size_bytes=1024)

    result = service.upload(
        source,  # type: ignore[arg-type]
        filename="../../roads data (final).geojson",
        content_type="application/geo+json",
    )

    assert source.read_sizes
    assert max(source.read_sizes) <= 5
    assert result.filename == "roads_data_final.geojson"
    assert result.stat.ref.state is ArtifactState.READY
    assert result.stat.size_bytes == len(payload)
    assert result.stat.checksum == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert result.artifact.uri == f"artifact://{result.stat.ref.key}"
    assert result.artifact.state == ArtifactLifecycleState.READY.value
    assert repository.artifacts == [result.artifact]

    with store.open(result.stat.ref) as handle:
        assert handle.read() == payload

    temporary_ref = ArtifactRef(key=result.stat.ref.key)
    with pytest.raises(KeyError):
        store.stat(temporary_ref)


def test_upload_service_rejects_size_hint_without_reading_source(tmp_path: Path) -> None:
    class UnreadableSource:
        def read(self, _size: int = -1) -> bytes:
            raise AssertionError("source must not be read")

    repository = FakeArtifactRepository()
    service = UploadService(
        LocalArtifactStore(tmp_path),
        repository,
        max_size_bytes=8,
    )

    with pytest.raises(UploadTooLargeError) as exc_info:
        service.upload(
            UnreadableSource(),  # type: ignore[arg-type]
            filename="roads.geojson",
            content_type="application/geo+json",
            size_hint=9,
        )

    assert exc_info.value.max_size_bytes == 8
    assert repository.artifacts == []
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_upload_service_enforces_stream_limit_and_cleans_partial_blob(tmp_path: Path) -> None:
    repository = FakeArtifactRepository()
    service = UploadService(
        LocalArtifactStore(tmp_path, chunk_size=3),
        repository,
        max_size_bytes=8,
    )

    with pytest.raises(UploadTooLargeError):
        service.upload(
            BytesIO(b"123456789"),
            filename="roads.geojson",
            content_type=None,
        )

    assert repository.artifacts == []
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_upload_service_compensates_blob_when_metadata_persistence_fails(
    tmp_path: Path,
) -> None:
    service = UploadService(
        LocalArtifactStore(tmp_path, chunk_size=4),
        FailingArtifactRepository(),
        max_size_bytes=64,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.upload(
            BytesIO(b"payload"),
            filename="source.gpkg",
            content_type="application/geopackage+sqlite3",
        )

    assert not any(path.is_file() for path in tmp_path.rglob("*"))
