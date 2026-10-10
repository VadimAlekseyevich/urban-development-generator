from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from io import BytesIO
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from backend.app.adapters.s3_artifact_store import (
    S3ArtifactStore,
    S3ArtifactStoreError,
)
from core.urban_generator.domain import (
    ArtifactContractError,
    ArtifactRef,
    ArtifactState,
    ArtifactStore,
)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str], datetime]] = {}
        self.fail_delete_once = False

    def transport(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
        parsed = urlsplit(str(request.url))
        path = unquote(parsed.path)

        if request.method == "GET" and path == "/bucket":
            query = parse_qs(parsed.query)
            prefix = query.get("prefix", [""])[0]
            start_after = query.get("start-after", [""])[0]
            max_keys = int(query.get("max-keys", ["1000"])[0])
            keys = [
                key
                for key in sorted(self.objects)
                if key.startswith(prefix) and key > start_after
            ]
            page = keys[:max_keys]
            is_truncated = len(keys) > len(page)
            parts = [
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
                f"<IsTruncated>{str(is_truncated).lower()}</IsTruncated>",
            ]
            for key in page:
                modified = (
                    self.objects[key][2]
                    .astimezone(UTC)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                parts.append(
                    f"<Contents><Key>{key}</Key>"
                    f"<LastModified>{modified}</LastModified></Contents>"
                )
            parts.append("</ListBucketResult>")
            return httpx.Response(
                200,
                content="".join(parts).encode(),
                request=request,
            )

        assert path.startswith("/bucket/")
        key = path.removeprefix("/bucket/")
        if request.method == "HEAD":
            item = self.objects.get(key)
            if item is None:
                return httpx.Response(404, request=request)
            data, metadata, modified = item
            headers = {
                **metadata,
                "content-length": str(len(data)),
                "last-modified": format_datetime(modified, usegmt=True),
            }
            return httpx.Response(200, headers=headers, request=request)

        if request.method == "GET":
            item = self.objects.get(key)
            if item is None:
                return httpx.Response(404, request=request)
            return httpx.Response(200, content=item[0], request=request)

        if request.method == "DELETE":
            if self.fail_delete_once:
                self.fail_delete_once = False
                return httpx.Response(500, text="boom", request=request)
            self.objects.pop(key, None)
            return httpx.Response(204, request=request)

        if request.method == "PUT" and "x-amz-copy-source" in request.headers:
            if request.headers.get("if-none-match") == "*" and key in self.objects:
                return httpx.Response(412, request=request)
            source = unquote(request.headers["x-amz-copy-source"]).removeprefix(
                "/bucket/"
            )
            item = self.objects.get(source)
            if item is None:
                return httpx.Response(404, request=request)
            data, metadata, _ = item
            self.objects[key] = (data, dict(metadata), datetime.now(UTC))
            return httpx.Response(
                200,
                content=b"<CopyObjectResult/>",
                request=request,
            )

        if request.method == "PUT":
            data = request.read()
            metadata = {
                name: value
                for name, value in request.headers.items()
                if name.startswith("x-amz-meta-") or name == "content-type"
            }
            self.objects[key] = (data, metadata, datetime.now(UTC))
            return httpx.Response(200, request=request)

        return httpx.Response(405, request=request)


def make_store(
    fake: FakeS3,
    *,
    clock: Callable[[], datetime] | None = None,
    chunk_size: int = 1024 * 1024,
) -> S3ArtifactStore:
    return S3ArtifactStore(
        endpoint_url="http://s3.local",
        bucket="bucket",
        access_key="access",
        secret_key="secret",
        prefix="root",
        client=httpx.Client(transport=httpx.MockTransport(fake.transport)),
        clock=clock,
        chunk_size=chunk_size,
    )


def test_s3_store_round_trip_promote_and_delete() -> None:
    fake = FakeS3()
    store = make_store(fake)
    assert isinstance(store, ArtifactStore)

    ref = ArtifactRef("runs/r1/result.bin")
    payload = b"hello"
    stat = store.put(
        ref,
        BytesIO(payload),
        content_type="application/octet-stream",
    )

    assert stat.checksum == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert store.open(ref).read() == payload
    ready = store.promote(ref)
    assert ready.ref.state is ArtifactState.READY
    assert store.stat(ready.ref) == ready
    assert store.promote(ref) == ready
    assert store.promote(ready.ref) == ready

    store.delete(ready.ref)
    store.delete(ready.ref)
    with pytest.raises(KeyError):
        store.stat(ready.ref)


def test_s3_store_promote_recovers_after_copy_before_delete() -> None:
    fake = FakeS3()
    store = make_store(fake)
    ref = ArtifactRef("runs/r1/interrupted.bin")
    store.put(ref, BytesIO(b"payload"))
    fake.fail_delete_once = True

    with pytest.raises(S3ArtifactStoreError, match="delete"):
        store.promote(ref)

    ready = store.promote(ref)
    assert ready.ref == ref.as_ready()
    assert store.open(ready.ref).read() == b"payload"
    with pytest.raises(KeyError):
        store.stat(ref)


def test_s3_store_never_overwrites_ready_content() -> None:
    fake = FakeS3()
    store = make_store(fake)
    ref = ArtifactRef("exports/r1/result.gpkg")
    store.put(ref, BytesIO(b"first"))
    store.promote(ref)

    with pytest.raises(
        ArtifactContractError,
        match="ready artifact already exists",
    ):
        store.put(ref, BytesIO(b"second"))

    assert store.open(ref.as_ready()).read() == b"first"


def test_s3_store_preserves_none_content_type() -> None:
    fake = FakeS3()
    store = make_store(fake)
    ref = ArtifactRef("uploads/blob.bin")

    stat = store.put(ref, BytesIO(b"x"))

    assert stat.content_type is None
    assert store.stat(ref).content_type is None


def test_s3_store_reads_input_in_bounded_chunks() -> None:
    class BoundedSource(BytesIO):
        def __init__(self, payload: bytes) -> None:
            super().__init__(payload)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            assert size > 0
            self.read_sizes.append(size)
            return super().read(size)

    fake = FakeS3()
    store = make_store(fake, chunk_size=4)
    source = BoundedSource(b"x" * 17)

    store.put(ArtifactRef("uploads/chunked.bin"), source)

    assert source.read_sizes
    assert max(source.read_sizes) == 4


def test_s3_store_stale_scan_is_run_scoped_and_mtime_safe() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    fake = FakeS3()
    store = make_store(fake, clock=lambda: now)
    old = ArtifactRef("runs/old/stages/root/a.bin")
    recent = ArtifactRef("runs/new/stages/root/b.bin")
    store.put(old, BytesIO(b"old"))
    store.put(recent, BytesIO(b"recent"))

    for key, (data, metadata, _) in list(fake.objects.items()):
        modified = now - timedelta(hours=3) if "old" in key else now
        fake.objects[key] = (data, metadata, modified)

    candidates = store.stale_run_refs(
        older_than=now - timedelta(hours=2),
        max_scan=10,
        max_results=10,
    )

    assert tuple(item.key for item in candidates) == (old.key,)
    assert store.has_run_ref(old)
    assert store.is_stale_run_ref(
        old,
        older_than=now - timedelta(hours=2),
    )
    assert not store.is_stale_run_ref(
        recent,
        older_than=now - timedelta(hours=2),
    )
