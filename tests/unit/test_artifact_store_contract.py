import hashlib
from dataclasses import dataclass
from io import BytesIO

import pytest

from core.urban_generator.domain import (
    ArtifactContractError,
    ArtifactRef,
    ArtifactState,
    ArtifactStat,
    ArtifactStore,
    require_temporary_artifact_ref,
)


@dataclass(frozen=True, slots=True)
class _Record:
    data: bytes
    stat: ArtifactStat


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._records: dict[ArtifactRef, _Record] = {}

    def put(
        self,
        ref: ArtifactRef,
        source: BytesIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        require_temporary_artifact_ref(ref)
        ready_ref = ref.as_ready()
        if ready_ref in self._records:
            raise ArtifactContractError("ready artifact already exists")

        payload = source.read()
        checksum = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        stat = ArtifactStat(
            ref=ref,
            size_bytes=len(payload),
            checksum=checksum,
            content_type=content_type,
        )
        self._records[ref] = _Record(data=payload, stat=stat)
        return stat

    def open(self, ref: ArtifactRef) -> BytesIO:
        return BytesIO(self._records[ref].data)

    def stat(self, ref: ArtifactRef) -> ArtifactStat:
        return self._records[ref].stat

    def delete(self, ref: ArtifactRef) -> None:
        self._records.pop(ref, None)

    def promote(self, ref: ArtifactRef) -> ArtifactStat:
        ready_ref = ref.as_ready()
        if ref.state is ArtifactState.READY:
            return self._records[ready_ref].stat

        temporary = self._records.get(ref)
        existing_ready = self._records.get(ready_ref)
        if temporary is None:
            if existing_ready is not None:
                return existing_ready.stat
            raise KeyError(ref)
        if existing_ready is not None:
            raise ArtifactContractError("ready artifact already exists")

        ready_stat = ArtifactStat(
            ref=ready_ref,
            size_bytes=temporary.stat.size_bytes,
            checksum=temporary.stat.checksum,
            content_type=temporary.stat.content_type,
        )
        self._records.pop(ref)
        self._records[ready_ref] = _Record(data=temporary.data, stat=ready_stat)
        return ready_stat


def test_in_memory_adapter_passes_artifact_store_contract() -> None:
    store = InMemoryArtifactStore()
    assert isinstance(store, ArtifactStore)

    temporary_ref = ArtifactRef(key="runs/run-001/metrics.json")
    payload = b'{"population": 42000}'
    temporary_stat = store.put(
        temporary_ref,
        BytesIO(payload),
        content_type="application/json",
    )

    assert temporary_stat.ref.state is ArtifactState.TEMPORARY
    assert temporary_stat.size_bytes == len(payload)
    assert temporary_stat.checksum == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert store.open(temporary_ref).read() == payload
    assert store.stat(temporary_ref) == temporary_stat

    ready_stat = store.promote(temporary_ref)
    ready_ref = temporary_ref.as_ready()
    assert ready_stat.ref == ready_ref
    assert ready_stat.checksum == temporary_stat.checksum
    assert ready_stat.size_bytes == temporary_stat.size_bytes
    assert store.open(ready_ref).read() == payload

    with pytest.raises(KeyError):
        store.stat(temporary_ref)

    assert store.promote(temporary_ref) == ready_stat
    assert store.promote(ready_ref) == ready_stat

    store.delete(ready_ref)
    store.delete(ready_ref)
    with pytest.raises(KeyError):
        store.stat(ready_ref)


def test_put_requires_temporary_ref_and_never_overwrites_ready() -> None:
    store = InMemoryArtifactStore()
    temporary_ref = ArtifactRef(key="exports/run-001/result.gpkg")
    ready_ref = temporary_ref.as_ready()

    store.put(temporary_ref, BytesIO(b"first"))
    store.promote(temporary_ref)

    with pytest.raises(ArtifactContractError, match="put target must be temporary"):
        store.put(ready_ref, BytesIO(b"second"))

    with pytest.raises(ArtifactContractError, match="ready artifact already exists"):
        store.put(temporary_ref, BytesIO(b"second"))

    assert store.open(ready_ref).read() == b"first"


@pytest.mark.parametrize(
    "key",
    [
        "/tmp/result.bin",
        "../result.bin",
        "runs/../result.bin",
        "runs//result.bin",
        "C:\\temp\\result.bin",
        "s3://bucket/result.bin",
    ],
)
def test_artifact_ref_rejects_filesystem_and_storage_path_leakage(key: str) -> None:
    with pytest.raises(ArtifactContractError):
        ArtifactRef(key=key)


def test_artifact_ref_allows_logical_namespaces() -> None:
    ref = ArtifactRef(key="projects/p-001/runs/r-001/suitability.tif")
    assert ref.key == "projects/p-001/runs/r-001/suitability.tif"
    assert ref.state is ArtifactState.TEMPORARY


def test_artifact_stat_validates_checksum_and_size() -> None:
    ref = ArtifactRef(key="runs/r-001/report.json")

    with pytest.raises(ArtifactContractError, match="checksum must use sha256"):
        ArtifactStat(ref=ref, size_bytes=1, checksum="md5:bad")

    with pytest.raises(ArtifactContractError, match="non-negative"):
        ArtifactStat(
            ref=ref,
            size_bytes=-1,
            checksum="sha256:" + "0" * 64,
        )
