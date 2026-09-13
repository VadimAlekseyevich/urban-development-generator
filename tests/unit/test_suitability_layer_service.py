import hashlib
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

import numpy as np
import pytest
from rasterio.io import MemoryFile

from backend.app.application.suitability_layers import (
    SuitabilityArtifactContractError,
    SuitabilityArtifactNotFoundError,
    SuitabilityArtifactRecord,
    SuitabilityArtifactUnavailableError,
    SuitabilityLayerService,
)
from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    ArtifactStore,
    require_temporary_artifact_ref,
)
from core.urban_generator.suitability.aggregation import WeightedSuitabilityResult
from core.urban_generator.suitability.artifact import SuitabilityArtifactWriter
from core.urban_generator.suitability.config import (
    SuitabilityConfig,
    SuitabilityFactorConfig,
    SuitabilityNormalization,
    SuitabilityThresholds,
)
from core.urban_generator.suitability.factors import SuitabilityGridSpec
from core.urban_generator.suitability.hard_exclusion import HardExclusionMask


@dataclass(frozen=True, slots=True)
class _Stored:
    data: bytes
    stat: ArtifactStat


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self.records: dict[ArtifactRef, _Stored] = {}
        self.open_calls = 0

    def put(
        self,
        ref: ArtifactRef,
        source: BinaryIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        require_temporary_artifact_ref(ref)
        payload = source.read()
        checksum = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        stat = ArtifactStat(
            ref=ref,
            size_bytes=len(payload),
            checksum=checksum,
            content_type=content_type,
        )
        self.records[ref] = _Stored(payload, stat)
        return stat

    def open(self, ref: ArtifactRef) -> BinaryIO:
        self.open_calls += 1
        return BytesIO(self.records[ref].data)

    def stat(self, ref: ArtifactRef) -> ArtifactStat:
        return self.records[ref].stat

    def delete(self, ref: ArtifactRef) -> None:
        self.records.pop(ref, None)

    def promote(self, ref: ArtifactRef) -> ArtifactStat:
        ready_ref = ref.as_ready()
        if ref.state is ArtifactState.READY:
            return self.records[ready_ref].stat
        stored = self.records.pop(ref)
        stat = ArtifactStat(
            ref=ready_ref,
            size_bytes=stored.stat.size_bytes,
            checksum=stored.stat.checksum,
            content_type=stored.stat.content_type,
        )
        self.records[ready_ref] = _Stored(stored.data, stat)
        return stat


class FakeRepository:
    def __init__(self, record: SuitabilityArtifactRecord | None) -> None:
        self.record = record

    def get(self, *, artifact_id: uuid.UUID) -> SuitabilityArtifactRecord | None:
        if self.record is not None and self.record.id == artifact_id:
            return self.record
        return None


def _config() -> SuitabilityConfig:
    return SuitabilityConfig(
        version="suitability-v1",
        factors=(
            SuitabilityFactorConfig(
                code="landuse",
                weight=1.0,
                normalization=SuitabilityNormalization.IDENTITY,
            ),
        ),
        thresholds=SuitabilityThresholds(
            minimum_score=0.4,
            preferred_score=0.8,
        ),
    )


def _canonical_fixture(
    *,
    width: int = 4,
    height: int = 3,
) -> tuple[InMemoryArtifactStore, SuitabilityArtifactRecord]:
    grid = SuitabilityGridSpec(
        working_srid=3857,
        bounds=(0.0, 0.0, float(width * 100), float(height * 100)),
        width=width,
        height=height,
    )
    scores = np.linspace(0.05, 0.95, width * height, dtype=np.float64).reshape(
        height, width
    )
    valid = np.ones(grid.shape, dtype=np.bool_)
    hard = np.zeros(grid.shape, dtype=np.bool_)
    hard[0, 0] = True
    valid[0, 0] = False
    scores[0, 0] = 0.0
    if width * height > 1:
        valid.flat[1] = False
        scores.flat[1] = 0.0

    config = _config()
    result = WeightedSuitabilityResult(
        grid=grid,
        scores=scores,
        valid_mask=valid,
        hard_excluded_mask=hard,
        config_version=config.version,
        config_fingerprint=config.fingerprint,
        factor_versions=(("landuse", "landuse-v1"),),
    )
    hard_mask = HardExclusionMask(
        grid=grid,
        excluded=hard,
        source_codes=("boundary", "water"),
    )
    store = InMemoryArtifactStore()
    assert isinstance(store, ArtifactStore)
    artifact = SuitabilityArtifactWriter(tile_size=16).write(
        store,
        output_ref=ArtifactRef(key="suitability/unit/result.tif"),
        result=result,
        config=config,
        hard_mask=hard_mask,
    )
    record = SuitabilityArtifactRecord(
        id=uuid.uuid4(),
        uri=f"artifact://{artifact.stat.ref.key}",
        checksum=artifact.stat.checksum,
        size_bytes=artifact.stat.size_bytes,
        content_type=artifact.stat.content_type,
        state="ready",
    )
    return store, record


def test_metadata_reads_stats_factor_provenance_and_wgs84_extent() -> None:
    store, record = _canonical_fixture()
    service = SuitabilityLayerService(store, FakeRepository(record))

    metadata = service.get_metadata(artifact_id=record.id)

    assert metadata.artifact_id == record.id
    assert metadata.schema_version == "suitability-artifact-v1"
    assert metadata.working_srid == 3857
    assert metadata.width == 4
    assert metadata.height == 3
    assert metadata.statistics.total_cells == 12
    assert metadata.statistics.valid_cells == 10
    assert metadata.statistics.hard_excluded_cells == 1
    assert metadata.statistics.invalid_data_cells == 1
    assert metadata.statistics.minimum_score_threshold == pytest.approx(0.4)
    assert metadata.statistics.preferred_score_threshold == pytest.approx(0.8)
    assert metadata.factors[0].code == "landuse"
    assert metadata.factors[0].version == "landuse-v1"
    assert metadata.factors[0].normalization == "IDENTITY"
    assert metadata.hard_exclusion_source_codes == ("boundary", "water")
    west, south, east, north = metadata.wgs84_bounds
    assert west == pytest.approx(0.0)
    assert south == pytest.approx(0.0)
    assert east > west
    assert north > south
    assert len(metadata.image_coordinates_wgs84) == 4


def test_preview_encodes_valid_invalid_and_hard_excluded_alpha() -> None:
    store, record = _canonical_fixture()
    preview = SuitabilityLayerService(store, FakeRepository(record)).render_preview(
        artifact_id=record.id,
        max_dimension=128,
    )

    assert preview.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert preview.width == 4
    assert preview.height == 3
    with MemoryFile(preview.content) as memory_file:
        with memory_file.open() as dataset:
            assert dataset.count == 4
            rgba = dataset.read()
    alpha = rgba[3]
    assert alpha[0, 0] == 210
    assert alpha.flat[1] == 0
    assert np.all(alpha.flat[2:] == 255)


def test_preview_downsamples_to_bounded_max_dimension() -> None:
    store, record = _canonical_fixture(width=256, height=128)
    preview = SuitabilityLayerService(store, FakeRepository(record)).render_preview(
        artifact_id=record.id,
        max_dimension=128,
    )

    assert preview.width == 128
    assert preview.height == 64


def test_service_rejects_non_ready_artifact_before_opening_store() -> None:
    store, record = _canonical_fixture()
    unavailable = SuitabilityArtifactRecord(
        id=record.id,
        uri=record.uri,
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        content_type=record.content_type,
        state="temporary",
    )
    open_calls = store.open_calls

    with pytest.raises(SuitabilityArtifactUnavailableError, match="ready or referenced"):
        SuitabilityLayerService(store, FakeRepository(unavailable)).get_metadata(
            artifact_id=record.id
        )

    assert store.open_calls == open_calls


def test_service_rejects_oversized_artifact_before_opening_store() -> None:
    store, record = _canonical_fixture()
    open_calls = store.open_calls

    with pytest.raises(SuitabilityArtifactUnavailableError, match="read limit"):
        SuitabilityLayerService(
            store,
            FakeRepository(record),
            max_artifact_bytes=1,
        ).get_metadata(artifact_id=record.id)

    assert store.open_calls == open_calls


def test_service_rejects_noncanonical_bytes() -> None:
    store = InMemoryArtifactStore()
    temporary_ref = ArtifactRef(key="suitability/unit/not-a-raster.tif")
    store.put(temporary_ref, BytesIO(b"not a geotiff"), content_type="image/tiff")
    stat = store.promote(temporary_ref)
    record = SuitabilityArtifactRecord(
        id=uuid.uuid4(),
        uri=f"artifact://{stat.ref.key}",
        checksum=stat.checksum,
        size_bytes=stat.size_bytes,
        content_type=stat.content_type,
        state="ready",
    )

    with pytest.raises(SuitabilityArtifactContractError, match="readable canonical"):
        SuitabilityLayerService(store, FakeRepository(record)).get_metadata(
            artifact_id=record.id
        )


def test_service_reports_missing_artifact() -> None:
    store = InMemoryArtifactStore()
    missing_id = uuid.uuid4()

    with pytest.raises(SuitabilityArtifactNotFoundError):
        SuitabilityLayerService(store, FakeRepository(None)).get_metadata(
            artifact_id=missing_id
        )
