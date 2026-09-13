import hashlib
import os
from io import BytesIO
from pathlib import Path

import pytest

from backend.app.adapters.local_artifact_store import LocalArtifactStore
from core.urban_generator.domain import (
    ArtifactContractError,
    ArtifactRef,
    ArtifactState,
    ArtifactStore,
)


def test_local_store_round_trip_promote_and_delete(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    assert isinstance(store, ArtifactStore)

    ref = ArtifactRef("projects/p1/uploads/source.geojson")
    payload = b'{"type":"FeatureCollection","features":[]}'
    stat = store.put(ref, BytesIO(payload), content_type="application/geo+json")

    assert stat.ref == ref
    assert stat.size_bytes == len(payload)
    assert stat.checksum == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert stat.content_type == "application/geo+json"
    with store.open(ref) as stream:
        assert stream.read() == payload
    assert store.stat(ref) == stat

    ready = store.promote(ref)
    assert ready.ref.state is ArtifactState.READY
    assert ready.checksum == stat.checksum
    assert store.promote(ref) == ready
    assert store.promote(ref.as_ready()) == ready

    reopened_store = LocalArtifactStore(tmp_path / "artifacts")
    assert reopened_store.stat(ref.as_ready()) == ready
    assert reopened_store.open(ref.as_ready()).read() == payload
    with pytest.raises(KeyError):
        store.stat(ref)

    store.delete(ref.as_ready())
    store.delete(ref.as_ready())
    with pytest.raises(KeyError):
        store.open(ref.as_ready())


def test_put_replaces_temporary_without_overwriting_ready(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    ref = ArtifactRef("runs/r1/stage/report.json")

    first = store.put(ref, BytesIO(b"first"), content_type="text/plain")
    second = store.put(ref, BytesIO(b"second"), content_type="application/json")
    assert first.checksum != second.checksum
    assert store.open(ref).read() == b"second"
    assert store.stat(ref).content_type == "application/json"

    store.promote(ref)
    with pytest.raises(ArtifactContractError, match="ready artifact already exists"):
        store.put(ref, BytesIO(b"third"))
    assert store.open(ref.as_ready()).read() == b"second"


def test_put_streams_source_in_bounded_chunks(tmp_path: Path) -> None:
    class BoundedSource(BytesIO):
        def __init__(self, payload: bytes) -> None:
            super().__init__(payload)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            assert size > 0
            self.read_sizes.append(size)
            return super().read(size)

    payload = b"x" * 25
    source = BoundedSource(payload)
    store = LocalArtifactStore(tmp_path / "artifacts", chunk_size=8)
    stat = store.put(ArtifactRef("uploads/chunked.bin"), source)

    assert stat.size_bytes == len(payload)
    assert source.read_sizes
    assert max(source.read_sizes) == 8


def test_promote_recovers_after_interrupted_payload_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    ref = ArtifactRef("runs/r1/result.bin")
    store.put(ref, BytesIO(b"payload"), content_type="application/octet-stream")

    original_replace = os.replace
    calls = 0

    def fail_second_replace(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interrupted promotion")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="interrupted"):
        store.promote(ref)

    monkeypatch.setattr(os, "replace", original_replace)
    ready = store.promote(ref)
    assert ready.ref == ref.as_ready()
    assert store.open(ref.as_ready()).read() == b"payload"
    with pytest.raises(KeyError):
        store.stat(ref)


def test_safe_root_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = LocalArtifactStore(root)

    namespace = root / "temporary" / "projects"
    namespace.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactContractError, match="escapes configured storage root"):
        store.put(ArtifactRef("projects/escape.bin"), BytesIO(b"blocked"))
    assert not (outside / "escape.bin").exists()


def test_missing_metadata_and_payload_size_mismatch_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = LocalArtifactStore(root)
    ref = ArtifactRef("runs/r1/result.txt")
    store.put(ref, BytesIO(b"payload"), content_type="text/plain")

    metadata = root / ".metadata" / "temporary" / "runs" / "r1" / "result.txt.json"
    metadata.unlink()
    with pytest.raises(ArtifactContractError, match="metadata is missing"):
        store.stat(ref)

    store.put(ref, BytesIO(b"payload"), content_type="text/plain")
    payload = root / "temporary" / "runs" / "r1" / "result.txt"
    payload.write_bytes(b"tampered-and-longer")
    with pytest.raises(ArtifactContractError, match="size does not match"):
        store.stat(ref)
