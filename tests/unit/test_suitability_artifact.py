from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

import numpy as np
import pytest
from rasterio.io import MemoryFile

from core.urban_generator.domain.artifacts import (
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    require_temporary_artifact_ref,
)
from core.urban_generator.suitability import (
    HardExclusionMask,
    SuitabilityArtifactError,
    SuitabilityArtifactWriter,
    SuitabilityConfig,
    SuitabilityFactorConfig,
    SuitabilityGridSpec,
    SuitabilityNormalization,
    SuitabilityThresholds,
    WeightedSuitabilityResult,
)


@dataclass(frozen=True, slots=True)
class _Record:
    data: bytes
    stat: ArtifactStat


class _MemoryStore:
    def __init__(self) -> None:
        self.records: dict[ArtifactRef, _Record] = {}
        self.put_calls = 0

    def put(
        self,
        ref: ArtifactRef,
        source: BinaryIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        require_temporary_artifact_ref(ref)
        self.put_calls += 1
        payload = source.read()
        stat = ArtifactStat(
            ref=ref,
            size_bytes=len(payload),
            checksum=f"sha256:{hashlib.sha256(payload).hexdigest()}",
            content_type=content_type,
        )
        self.records[ref] = _Record(payload, stat)
        return stat

    def open(self, ref: ArtifactRef) -> BinaryIO:
        return BytesIO(self.records[ref].data)

    def stat(self, ref: ArtifactRef) -> ArtifactStat:
        return self.records[ref].stat

    def delete(self, ref: ArtifactRef) -> None:
        self.records.pop(ref, None)

    def promote(self, ref: ArtifactRef) -> ArtifactStat:
        ready_ref = ref.as_ready()
        if ref.state is ArtifactState.READY:
            return self.records[ready_ref].stat
        record = self.records.pop(ref)
        stat = ArtifactStat(
            ref=ready_ref,
            size_bytes=record.stat.size_bytes,
            checksum=record.stat.checksum,
            content_type=record.stat.content_type,
        )
        self.records[ready_ref] = _Record(record.data, stat)
        return stat


def _grid(*, width: int = 2, height: int = 3) -> SuitabilityGridSpec:
    return SuitabilityGridSpec(
        working_srid=32637,
        bounds=(500_000.0, 6_000_000.0, 500_020.0, 6_000_030.0),
        width=width,
        height=height,
    )


def _config() -> SuitabilityConfig:
    return SuitabilityConfig(
        version="v1",
        factors=(
            SuitabilityFactorConfig(
                code="slope",
                weight=0.4,
                normalization=SuitabilityNormalization.MIN_MAX,
                raw_min=0.0,
                raw_max=30.0,
            ),
            SuitabilityFactorConfig(
                code="landuse",
                weight=0.6,
                normalization=SuitabilityNormalization.IDENTITY,
            ),
        ),
        thresholds=SuitabilityThresholds(
            minimum_score=0.5,
            preferred_score=0.75,
        ),
    )


def _result(
    *,
    grid: SuitabilityGridSpec,
    config: SuitabilityConfig,
    scores: np.ndarray | None = None,
    valid_mask: np.ndarray | None = None,
    hard_mask: np.ndarray | None = None,
) -> WeightedSuitabilityResult:
    scores = (
        np.array([[0.2, 0.0], [0.8, 0.0], [1.0, 0.6]], dtype=np.float64)
        if scores is None
        else scores
    )
    valid_mask = (
        np.array([[True, False], [True, False], [True, True]], dtype=np.bool_)
        if valid_mask is None
        else valid_mask
    )
    hard_mask = (
        np.array([[False, True], [False, False], [False, False]], dtype=np.bool_)
        if hard_mask is None
        else hard_mask
    )
    return WeightedSuitabilityResult(
        grid=grid,
        scores=scores,
        valid_mask=valid_mask,
        hard_excluded_mask=hard_mask,
        config_version=config.version,
        config_fingerprint=config.fingerprint,
        factor_versions=(("slope", "v1"), ("landuse", "v2")),
    )


def test_writer_persists_canonical_geotiff_statistics_and_provenance() -> None:
    grid = _grid()
    config = _config()
    result = _result(grid=grid, config=config)
    hard_mask = HardExclusionMask(
        grid=grid,
        excluded=result.hard_excluded_mask,
        source_codes=("boundary", "water"),
    )
    store = _MemoryStore()
    output_ref = ArtifactRef(key="runs/run-001/suitability.tif")

    artifact = SuitabilityArtifactWriter(tile_size=16).write(
        store,
        output_ref=output_ref,
        result=result,
        config=config,
        hard_mask=hard_mask,
    )

    assert artifact.stat.ref == output_ref.as_ready()
    assert artifact.stat.content_type == "image/tiff"
    assert artifact.statistics.total_cells == 6
    assert artifact.statistics.valid_cells == 4
    assert artifact.statistics.hard_excluded_cells == 1
    assert artifact.statistics.invalid_data_cells == 1
    assert artifact.statistics.meets_minimum_cells == 3
    assert artifact.statistics.preferred_cells == 2
    assert artifact.statistics.min_score == pytest.approx(0.2)
    assert artifact.statistics.max_score == pytest.approx(1.0)
    assert artifact.statistics.mean_score == pytest.approx(0.65)
    assert artifact.provenance.config_fingerprint == config.fingerprint
    assert artifact.provenance.hard_exclusion_source_codes == ("boundary", "water")
    assert tuple(item.code for item in artifact.provenance.factors) == ("slope", "landuse")
    assert artifact.windows_written == 1
    assert artifact.max_window_cells == 6

    payload = store.open(output_ref.as_ready()).read()
    with MemoryFile(payload) as memory_file, memory_file.open() as dataset:
        assert dataset.count == 2
        assert dataset.dtypes == ("float32", "float32")
        assert dataset.crs is not None
        assert dataset.crs.to_epsg() == 32637
        assert dataset.bounds.left == pytest.approx(grid.bounds[0])
        assert dataset.bounds.bottom == pytest.approx(grid.bounds[1])
        assert dataset.bounds.right == pytest.approx(grid.bounds[2])
        assert dataset.bounds.top == pytest.approx(grid.bounds[3])
        assert dataset.descriptions == ("suitability_score", "cell_status")
        np.testing.assert_allclose(dataset.read(1), result.scores.astype(np.float32))
        np.testing.assert_array_equal(
            dataset.read(2),
            np.array([[1.0, 2.0], [1.0, 0.0], [1.0, 1.0]], dtype=np.float32),
        )
        tags = dataset.tags()

    statistics_tag = json.loads(tags["statistics_json"])
    provenance_tag = json.loads(tags["provenance_json"])
    status_codes = json.loads(tags["status_codes_json"])
    assert tags["schema_version"] == "suitability-artifact-v1"
    assert statistics_tag["valid_cells"] == 4
    assert provenance_tag["config_fingerprint"] == config.fingerprint
    assert provenance_tag["hard_exclusion_source_codes"] == ["boundary", "water"]
    assert status_codes == {"hard_excluded": 2, "invalid_data": 0, "valid": 1}


def test_writer_reports_empty_valid_statistics_without_nan() -> None:
    grid = _grid()
    config = _config()
    hard = np.array([[True, False], [False, False], [False, False]], dtype=np.bool_)
    result = _result(
        grid=grid,
        config=config,
        scores=np.zeros(grid.shape, dtype=np.float64),
        valid_mask=np.zeros(grid.shape, dtype=np.bool_),
        hard_mask=hard,
    )
    hard_mask = HardExclusionMask(
        grid=grid,
        excluded=hard,
        source_codes=("boundary",),
    )
    store = _MemoryStore()

    artifact = SuitabilityArtifactWriter(tile_size=16).write(
        store,
        output_ref=ArtifactRef(key="runs/run-empty/suitability.tif"),
        result=result,
        config=config,
        hard_mask=hard_mask,
    )

    stats = artifact.statistics
    assert stats.valid_cells == 0
    assert stats.min_score is None
    assert stats.max_score is None
    assert stats.mean_score is None
    assert stats.p05_score is None
    assert stats.p50_score is None
    assert stats.p95_score is None
    assert stats.meets_minimum_cells == 0
    assert stats.preferred_cells == 0


def test_writer_validates_inputs_and_cell_budget_before_storage_write() -> None:
    grid = _grid()
    config = _config()
    result = _result(grid=grid, config=config)
    store = _MemoryStore()
    mismatched_hard = HardExclusionMask(
        grid=grid,
        excluded=np.zeros(grid.shape, dtype=np.bool_),
        source_codes=("boundary",),
    )

    with pytest.raises(SuitabilityArtifactError, match="hard mask cells must match"):
        SuitabilityArtifactWriter(tile_size=16).write(
            store,
            output_ref=ArtifactRef(key="runs/run-bad/suitability.tif"),
            result=result,
            config=config,
            hard_mask=mismatched_hard,
        )
    assert store.put_calls == 0

    correct_hard = HardExclusionMask(
        grid=grid,
        excluded=result.hard_excluded_mask,
        source_codes=("boundary",),
    )
    with pytest.raises(SuitabilityArtifactError, match="grid cell limit exceeded"):
        SuitabilityArtifactWriter(tile_size=16, max_cells=5).write(
            store,
            output_ref=ArtifactRef(key="runs/run-large/suitability.tif"),
            result=result,
            config=config,
            hard_mask=correct_hard,
        )
    assert store.put_calls == 0


def test_writer_uses_bounded_tiles() -> None:
    grid = SuitabilityGridSpec(
        working_srid=32637,
        bounds=(0.0, 0.0, 18.0, 20.0),
        width=18,
        height=20,
    )
    config = _config()
    valid = np.ones(grid.shape, dtype=np.bool_)
    hard = np.zeros(grid.shape, dtype=np.bool_)
    result = WeightedSuitabilityResult(
        grid=grid,
        scores=np.full(grid.shape, 0.5, dtype=np.float64),
        valid_mask=valid,
        hard_excluded_mask=hard,
        config_version=config.version,
        config_fingerprint=config.fingerprint,
        factor_versions=(("slope", "v1"), ("landuse", "v2")),
    )
    hard_mask = HardExclusionMask(
        grid=grid,
        excluded=hard,
        source_codes=("boundary",),
    )

    artifact = SuitabilityArtifactWriter(tile_size=16).write(
        _MemoryStore(),
        output_ref=ArtifactRef(key="runs/run-tiled/suitability.tif"),
        result=result,
        config=config,
        hard_mask=hard_mask,
    )

    assert artifact.windows_written == 4
    assert artifact.max_window_cells == 256
