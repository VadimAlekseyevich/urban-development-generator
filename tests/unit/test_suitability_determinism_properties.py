from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

import numpy as np
import pytest
from rasterio.io import MemoryFile
from shapely.geometry import LineString, box

from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    ConfigRef,
    CorrelationMetadata,
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.domain.artifacts import require_temporary_artifact_ref
from core.urban_generator.suitability import (
    DEMSlopeFactor,
    DEMSlopeNoDataPolicy,
    DEMSlopeWindow,
    HardExclusionBoundary,
    HardExclusionMask,
    HardExclusionRasterLayer,
    LanduseClassWeight,
    LanduseClassWeights,
    LanduseFactor,
    LanduseUnknownClassPolicy,
    LanduseWindow,
    RoadProximityFactor,
    RoadProximityIndex,
    SuitabilityArtifactWriter,
    SuitabilityConfig,
    SuitabilityFactorConfig,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
    SuitabilityNormalization,
    SuitabilityThresholds,
    aggregate_weighted_suitability,
    build_hard_exclusion_mask,
)

_PROPERTY_SEEDS = (7, 19, 41, 73, 101, 2026)
_FACTOR_SEEDS = (11, 2026, 9091)


@dataclass(frozen=True, slots=True)
class _StoredArtifact:
    data: bytes
    stat: ArtifactStat


class _MemoryStore:
    def __init__(self) -> None:
        self.records: dict[ArtifactRef, _StoredArtifact] = {}

    def put(
        self,
        ref: ArtifactRef,
        source: BinaryIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        require_temporary_artifact_ref(ref)
        payload = source.read()
        stat = ArtifactStat(
            ref=ref,
            size_bytes=len(payload),
            checksum=f"sha256:{hashlib.sha256(payload).hexdigest()}",
            content_type=content_type,
        )
        self.records[ref] = _StoredArtifact(payload, stat)
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
        self.records[ready_ref] = _StoredArtifact(record.data, stat)
        return stat


class _DEMSource:
    def __init__(
        self,
        grid: SuitabilityGridSpec,
        values: tuple[tuple[float | None, ...], ...],
    ) -> None:
        self.grid = grid
        self.values = values

    def read_window(
        self,
        *,
        window: DEMSlopeWindow,
    ) -> tuple[tuple[float | None, ...], ...]:
        return tuple(
            tuple(
                self.values[row][col]
                for col in range(window.col_off, window.col_off + window.width)
            )
            for row in range(window.row_off, window.row_off + window.height)
        )


class _LanduseSource:
    def __init__(
        self,
        grid: SuitabilityGridSpec,
        values: tuple[tuple[str | None, ...], ...],
    ) -> None:
        self.grid = grid
        self.values = values

    def read_window(
        self,
        *,
        window: LanduseWindow,
    ) -> tuple[tuple[str | None, ...], ...]:
        return tuple(
            tuple(
                self.values[row][col]
                for col in range(window.col_off, window.col_off + window.width)
            )
            for row in range(window.row_off, window.row_off + window.height)
        )


def _grid(*, width: int, height: int) -> SuitabilityGridSpec:
    return SuitabilityGridSpec(
        working_srid=32637,
        bounds=(
            500_000.0,
            6_000_000.0,
            500_000.0 + width * 10.0,
            6_000_000.0 + height * 10.0,
        ),
        width=width,
        height=height,
    )


def _snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000001301"),
        project=ProjectRef(project_id=uuid.UUID("00000000-0000-0000-0000-000000001302")),
        settings=ProjectSettings(working_srid=32637),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:determinism",
        ),
        roads=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.ROADS,
                source_ref="synthetic:roads:determinism",
            ),
        ),
        landuse=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.LANDUSE,
                source_ref="synthetic:landuse:determinism",
            ),
        ),
        dem=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.DEM,
                source_ref="synthetic:dem:determinism",
            ),
        ),
    )


def _context() -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000001303"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=32637,
        config_refs=(ConfigRef(name="suitability", ref="synthetic:suitability:v1"),),
        correlation=CorrelationMetadata(correlation_id="s04-t13-determinism"),
    )


def _config() -> SuitabilityConfig:
    return SuitabilityConfig(
        version="determinism-v1",
        factors=(
            SuitabilityFactorConfig(
                code="landuse",
                weight=0.5,
                normalization=SuitabilityNormalization.IDENTITY,
            ),
            SuitabilityFactorConfig(
                code="slope",
                weight=0.3,
                normalization=SuitabilityNormalization.MIN_MAX,
                raw_min=0.0,
                raw_max=45.0,
            ),
            SuitabilityFactorConfig(
                code="road_proximity",
                weight=0.2,
                normalization=SuitabilityNormalization.INVERTED_MIN_MAX,
                raw_min=0.0,
                raw_max=1000.0,
            ),
        ),
        thresholds=SuitabilityThresholds(
            minimum_score=0.45,
            preferred_score=0.75,
        ),
    )


def _result(
    *,
    code: str,
    version: str,
    grid: SuitabilityGridSpec,
    values: np.ndarray,
    valid_mask: np.ndarray,
) -> SuitabilityFactorResult:
    return SuitabilityFactorResult(
        code=code,
        version=version,
        grid=grid,
        values=values,
        valid_mask=valid_mask,
    )


def _random_aggregate_case(
    *,
    seed: int,
    width: int | None = None,
    height: int | None = None,
):
    rng = np.random.default_rng(seed)
    resolved_width = width if width is not None else int(rng.integers(3, 13))
    resolved_height = height if height is not None else int(rng.integers(3, 13))
    grid = _grid(width=resolved_width, height=resolved_height)
    config = _config()

    hard_values = rng.random(grid.shape) < 0.18
    hard_mask = HardExclusionMask(
        grid=grid,
        excluded=hard_values,
        source_codes=("boundary", "water", "protected"),
    )

    landuse_values = rng.random(grid.shape)
    slope_values = rng.uniform(-10.0, 60.0, size=grid.shape)
    road_values = rng.uniform(-100.0, 1200.0, size=grid.shape)
    landuse_valid = rng.random(grid.shape) >= 0.08
    slope_valid = rng.random(grid.shape) >= 0.10
    road_valid = rng.random(grid.shape) >= 0.06

    landuse = _result(
        code="landuse",
        version="landuse-v3",
        grid=grid,
        values=landuse_values,
        valid_mask=landuse_valid,
    )
    slope = _result(
        code="slope",
        version="slope-v2",
        grid=grid,
        values=slope_values,
        valid_mask=slope_valid,
    )
    road = _result(
        code="road_proximity",
        version="roads-v4",
        grid=grid,
        values=road_values,
        valid_mask=road_valid,
    )
    return (
        grid,
        config,
        hard_mask,
        landuse,
        slope,
        road,
        landuse_values,
        slope_values,
        road_values,
        landuse_valid,
        slope_valid,
        road_valid,
    )


@pytest.mark.parametrize("seed", _PROPERTY_SEEDS)
def test_weighted_aggregation_is_deterministic_and_hard_mask_dominates(seed: int) -> None:
    (
        grid,
        config,
        hard_mask,
        landuse,
        slope,
        road,
        landuse_values,
        slope_values,
        road_values,
        landuse_valid,
        slope_valid,
        road_valid,
    ) = _random_aggregate_case(seed=seed)

    first = aggregate_weighted_suitability(
        grid=grid,
        config=config,
        hard_mask=hard_mask,
        factor_results=(landuse, slope, road),
    )
    permuted = aggregate_weighted_suitability(
        grid=grid,
        config=config,
        hard_mask=hard_mask,
        factor_results=(road, landuse, slope),
    )

    np.testing.assert_array_equal(first.scores, permuted.scores)
    np.testing.assert_array_equal(first.valid_mask, permuted.valid_mask)
    np.testing.assert_array_equal(first.hard_excluded_mask, permuted.hard_excluded_mask)
    assert first.factor_versions == permuted.factor_versions
    assert first.diagnostics == permuted.diagnostics

    expected_valid = (
        ~hard_mask.excluded
        & landuse_valid
        & slope_valid
        & road_valid
    )
    expected = (
        0.5 * landuse_values
        + 0.3 * np.clip(slope_values / 45.0, 0.0, 1.0)
        + 0.2 * (1.0 - np.clip(road_values / 1000.0, 0.0, 1.0))
    )
    expected[~expected_valid] = 0.0

    np.testing.assert_array_equal(first.valid_mask, expected_valid)
    np.testing.assert_allclose(first.scores, expected, rtol=0.0, atol=1e-15)
    assert not np.any(first.valid_mask & hard_mask.excluded)
    assert np.all(first.scores[hard_mask.excluded] == 0.0)
    assert np.all(first.scores[~first.valid_mask] == 0.0)
    assert np.all((first.scores[first.valid_mask] >= 0.0))
    assert np.all((first.scores[first.valid_mask] <= 1.0))
    assert (
        first.valid_count
        + first.hard_excluded_count
        + first.invalid_data_count
        == grid.cell_count
    )


@pytest.mark.parametrize("seed", _PROPERTY_SEEDS)
def test_hard_mask_union_is_monotone_idempotent_and_order_independent(seed: int) -> None:
    rng = np.random.default_rng(seed)
    grid = _grid(width=9, height=8)
    boundary = HardExclusionBoundary(
        geometry=box(*grid.bounds),
        working_srid=grid.working_srid,
    )
    first_values = rng.random(grid.shape) < 0.24
    second_values = rng.random(grid.shape) < 0.31
    first_layer = HardExclusionRasterLayer(
        code="constraint_a",
        grid=grid,
        excluded=first_values,
    )
    first_copy = HardExclusionRasterLayer(
        code="constraint_a_copy",
        grid=grid,
        excluded=first_values,
    )
    second_layer = HardExclusionRasterLayer(
        code="constraint_b",
        grid=grid,
        excluded=second_values,
    )

    first_only = build_hard_exclusion_mask(
        grid=grid,
        boundary=boundary,
        raster_layers=(first_layer,),
    )
    combined = build_hard_exclusion_mask(
        grid=grid,
        boundary=boundary,
        raster_layers=(first_layer, second_layer),
    )
    reversed_layers = build_hard_exclusion_mask(
        grid=grid,
        boundary=boundary,
        raster_layers=(second_layer, first_layer),
    )
    duplicated = build_hard_exclusion_mask(
        grid=grid,
        boundary=boundary,
        raster_layers=(first_layer, first_copy),
    )

    np.testing.assert_array_equal(first_only.excluded, first_values)
    np.testing.assert_array_equal(combined.excluded, first_values | second_values)
    np.testing.assert_array_equal(reversed_layers.excluded, combined.excluded)
    np.testing.assert_array_equal(duplicated.excluded, first_only.excluded)
    assert np.all(~first_only.excluded | combined.excluded)
    assert combined.excluded_count >= first_only.excluded_count


@pytest.mark.parametrize("seed", _PROPERTY_SEEDS)
def test_boundary_exclusion_matches_cell_center_membership(seed: int) -> None:
    rng = np.random.default_rng(seed)
    grid = _grid(width=10, height=9)
    left_cells = int(rng.integers(0, 3))
    right_cells = int(rng.integers(0, 3))
    bottom_cells = int(rng.integers(0, 3))
    top_cells = int(rng.integers(0, 3))
    min_x, min_y, max_x, max_y = grid.bounds
    boundary = HardExclusionBoundary(
        geometry=box(
            min_x + left_cells * grid.cell_width_m,
            min_y + bottom_cells * grid.cell_height_m,
            max_x - right_cells * grid.cell_width_m,
            max_y - top_cells * grid.cell_height_m,
        ),
        working_srid=grid.working_srid,
    )

    mask = build_hard_exclusion_mask(grid=grid, boundary=boundary)

    x_centers = min_x + (np.arange(grid.width) + 0.5) * grid.cell_width_m
    y_centers = max_y - (np.arange(grid.height) + 0.5) * grid.cell_height_m
    inside_x = (x_centers >= boundary.geometry.bounds[0]) & (
        x_centers <= boundary.geometry.bounds[2]
    )
    inside_y = (y_centers >= boundary.geometry.bounds[1]) & (
        y_centers <= boundary.geometry.bounds[3]
    )
    expected_excluded = ~np.logical_and.outer(inside_y, inside_x)

    np.testing.assert_array_equal(mask.excluded, expected_excluded)
    assert mask.excluded_count + mask.developable_count == grid.cell_count
    assert mask.excluded_fraction == pytest.approx(mask.excluded_count / grid.cell_count)


@pytest.mark.parametrize("seed", _FACTOR_SEEDS)
def test_factor_outputs_do_not_depend_on_tile_partition_or_road_order(seed: int) -> None:
    rng = np.random.default_rng(seed)
    grid = _grid(width=7, height=6)
    snapshot = _snapshot()
    context = _context()

    dem_array = rng.normal(loc=100.0, scale=12.0, size=grid.shape)
    dem_missing = rng.random(grid.shape) < 0.09
    dem_values = tuple(
        tuple(None if dem_missing[row, col] else float(dem_array[row, col]) for col in range(7))
        for row in range(6)
    )
    dem_small = DEMSlopeFactor(
        source=_DEMSource(grid, dem_values),
        nodata_policy=DEMSlopeNoDataPolicy.ONE_SIDED,
        tile_size=2,
    ).evaluate(grid=grid, snapshot=snapshot, context=context)
    dem_large = DEMSlopeFactor(
        source=_DEMSource(grid, dem_values),
        nodata_policy=DEMSlopeNoDataPolicy.ONE_SIDED,
        tile_size=5,
    ).evaluate(grid=grid, snapshot=snapshot, context=context)
    np.testing.assert_array_equal(dem_small.valid_mask, dem_large.valid_mask)
    np.testing.assert_allclose(dem_small.values, dem_large.values, rtol=0.0, atol=1e-12)

    class_names: tuple[str | None, ...] = (
        "residential",
        "commercial",
        "industrial",
        "green",
        "unknown",
        None,
    )
    class_indexes = rng.integers(0, len(class_names), size=grid.shape)
    landuse_values = tuple(
        tuple(class_names[int(class_indexes[row, col])] for col in range(7))
        for row in range(6)
    )
    weights = LanduseClassWeights(
        version="generic-landuse-v1",
        classes=(
            LanduseClassWeight("residential", 0.9),
            LanduseClassWeight("commercial", 0.8),
            LanduseClassWeight("industrial", 0.3),
            LanduseClassWeight("green", 0.6),
        ),
        unknown_policy=LanduseUnknownClassPolicy.USE_DEFAULT,
        default_score=0.4,
    )
    landuse_small = LanduseFactor(
        source=_LanduseSource(grid, landuse_values),
        weights=weights,
        tile_size=2,
    ).evaluate(grid=grid, snapshot=snapshot, context=context)
    landuse_large = LanduseFactor(
        source=_LanduseSource(grid, landuse_values),
        weights=weights,
        tile_size=5,
    ).evaluate(grid=grid, snapshot=snapshot, context=context)
    np.testing.assert_array_equal(landuse_small.valid_mask, landuse_large.valid_mask)
    np.testing.assert_array_equal(landuse_small.values, landuse_large.values)

    roads = (
        LineString([(500_015.0, 6_000_000.0), (500_015.0, 6_000_060.0)]),
        LineString([(500_000.0, 6_000_025.0), (500_070.0, 6_000_025.0)]),
        LineString([(500_055.0, 6_000_000.0), (500_055.0, 6_000_060.0)]),
    )
    road_small = RoadProximityFactor(
        index=RoadProximityIndex(roads=roads, working_srid=grid.working_srid),
        tile_size=2,
    ).evaluate(grid=grid, snapshot=snapshot, context=context)
    road_large = RoadProximityFactor(
        index=RoadProximityIndex(roads=tuple(reversed(roads)), working_srid=grid.working_srid),
        tile_size=5,
    ).evaluate(grid=grid, snapshot=snapshot, context=context)
    np.testing.assert_array_equal(road_small.valid_mask, road_large.valid_mask)
    np.testing.assert_allclose(road_small.values, road_large.values, rtol=0.0, atol=1e-12)


def test_artifact_raster_and_statistics_are_partition_independent() -> None:
    (
        grid,
        config,
        hard_mask,
        landuse,
        slope,
        road,
        *_unused,
    ) = _random_aggregate_case(seed=13_013, width=35, height=29)
    result = aggregate_weighted_suitability(
        grid=grid,
        config=config,
        hard_mask=hard_mask,
        factor_results=(landuse, slope, road),
    )

    first_store = _MemoryStore()
    second_store = _MemoryStore()
    first_ref = ArtifactRef(key="runs/determinism/tiles-16.tif")
    second_ref = ArtifactRef(key="runs/determinism/tiles-32.tif")
    first = SuitabilityArtifactWriter(tile_size=16).write(
        first_store,
        output_ref=first_ref,
        result=result,
        config=config,
        hard_mask=hard_mask,
    )
    second = SuitabilityArtifactWriter(tile_size=32).write(
        second_store,
        output_ref=second_ref,
        result=result,
        config=config,
        hard_mask=hard_mask,
    )

    assert first.statistics == second.statistics
    assert first.provenance == second.provenance
    assert first.windows_written != second.windows_written

    first_payload = first_store.open(first_ref.as_ready()).read()
    second_payload = second_store.open(second_ref.as_ready()).read()
    with MemoryFile(first_payload) as first_file, MemoryFile(second_payload) as second_file:
        with first_file.open() as first_dataset, second_file.open() as second_dataset:
            np.testing.assert_array_equal(first_dataset.read(1), second_dataset.read(1))
            np.testing.assert_array_equal(first_dataset.read(2), second_dataset.read(2))
            assert first_dataset.tags() == second_dataset.tags()
            assert first_dataset.crs == second_dataset.crs
            assert first_dataset.transform == second_dataset.transform
            np.testing.assert_array_equal(
                first_dataset.read(1),
                result.scores.astype(np.float32),
            )

    valid_scores = result.scores[result.valid_mask]
    statistics = first.statistics
    assert statistics.total_cells == grid.cell_count
    assert statistics.valid_cells == int(np.count_nonzero(result.valid_mask))
    assert statistics.hard_excluded_cells == int(np.count_nonzero(hard_mask.excluded))
    assert statistics.invalid_data_cells == int(
        np.count_nonzero(~result.valid_mask & ~hard_mask.excluded)
    )
    assert statistics.min_score == pytest.approx(float(np.min(valid_scores)))
    assert statistics.max_score == pytest.approx(float(np.max(valid_scores)))
    assert statistics.mean_score == pytest.approx(float(np.mean(valid_scores, dtype=np.float64)))
    expected_quantiles = np.quantile(valid_scores, (0.05, 0.50, 0.95), method="linear")
    assert statistics.p05_score == pytest.approx(float(expected_quantiles[0]))
    assert statistics.p50_score == pytest.approx(float(expected_quantiles[1]))
    assert statistics.p95_score == pytest.approx(float(expected_quantiles[2]))
    assert statistics.meets_minimum_cells == int(
        np.count_nonzero(valid_scores >= config.thresholds.minimum_score)
    )
    assert config.thresholds.preferred_score is not None
    assert statistics.preferred_cells == int(
        np.count_nonzero(valid_scores >= config.thresholds.preferred_score)
    )
